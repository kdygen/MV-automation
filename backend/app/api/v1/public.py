"""Public (unauthenticated) endpoints for the customer-facing funnel.

These are scoped by the company's public slug or an unguessable quote token, and will
sit behind rate limiting at the deployment milestone. No tenant data is ever returned
beyond what belongs to the submitting customer's own request/quote.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import (
    AnyEmailProvider,
    distance_provider_dep,
    email_provider_dep,
    quote_public_url,
)
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.ratelimit import rate_limited
from app.db.session import get_db
from app.models import Company, Lead, MovingRequest, Quote, QuoteStatus
from app.pricing import PricingInputError
from app.providers.distance import DistanceProvider
from app.schemas.intake import IntakeResponse, MovingRequestIn, MovingRequestOut
from app.schemas.quotes import AcceptQuoteResponse, QuotePublicOut, QuoteSummaryPublic
from app.services import intake as intake_service
from app.services import notifications
from app.services import quotes as quote_service

logger = get_logger(__name__)

router = APIRouter(prefix="/public", tags=["public"])

# Public POSTs are internet-exposed with no auth; per-IP sliding-window limits damp
# abuse (form spam, token brute-forcing) without touching legitimate customers.
_limit_submit = rate_limited("public_submit", max_requests=20, window_seconds=600)
_limit_quote_action = rate_limited("public_quote_action", max_requests=30, window_seconds=600)


def _public_quote_summary(quote: Quote) -> QuoteSummaryPublic:
    """What the intake response exposes; drafts hide price and token."""
    if quote.status is QuoteStatus.DRAFT:
        return QuoteSummaryPublic(status="pending_review")
    return QuoteSummaryPublic(
        status=quote.status.value,
        public_token=quote.public_token,
        currency=quote.currency,
        amount_min_cents=quote.amount_min_cents,
        amount_max_cents=quote.amount_max_cents,
        estimated_hours=quote.estimated_hours,
        crew_size=quote.crew_size,
        line_items=list(quote.line_items),
        valid_until=quote.valid_until,
    )


@router.post(
    "/{company_slug}/requests",
    response_model=IntakeResponse,
    status_code=201,
    dependencies=[Depends(_limit_submit)],
)
def submit_moving_request(
    company_slug: str,
    payload: MovingRequestIn,
    db: Session = Depends(get_db),
    distance_provider: DistanceProvider = Depends(distance_provider_dep),
    email_provider: AnyEmailProvider = Depends(email_provider_dep),
    settings: Settings = Depends(get_settings),
) -> IntakeResponse:
    """Accept a moving request; instant-quote it when possible.

    - Normal companies: quote is created as ``sent`` and returned with amounts + link.
    - Review-mode companies: quote is created as ``draft``; response says
      ``pending_review`` with no price; the owner is emailed to approve it.
    - Unpriceable requests (e.g. unknown distance): stored ``pending`` with no quote —
      the lead is never lost.
    """
    company = intake_service.get_company_by_slug(db, company_slug)
    lead, request = intake_service.create_moving_request(
        db, company=company, payload=payload, distance_provider=distance_provider
    )

    quote_payload: QuoteSummaryPublic | None = None
    try:
        quote = quote_service.create_quote_for_request(
            db, company=company, request=request, lead=lead
        )
    except PricingInputError as exc:
        logger.info("Request %s not instantly quotable: %s", request.id, exc)
    else:
        quote_payload = _public_quote_summary(quote)
        if quote.status is QuoteStatus.SENT:
            notifications.send_quote_to_customer(
                email_provider,
                quote=quote,
                lead=lead,
                company=company,
                quote_url=quote_public_url(settings, quote.public_token),
            )
        else:
            notifications.send_review_needed_to_company(
                email_provider, lead=lead, company=company
            )

    return IntakeResponse(
        lead_id=lead.id,
        request=MovingRequestOut.model_validate(request),
        quote=quote_payload.model_dump(mode="json") if quote_payload else None,
    )


def _load_quote_context(
    db: Session, quote: Quote
) -> tuple[MovingRequest, Lead, Company]:
    request = db.get(MovingRequest, quote.moving_request_id)
    lead = db.get(Lead, request.lead_id) if request else None
    company = db.get(Company, quote.company_id)
    assert request is not None and lead is not None and company is not None
    return request, lead, company


@router.get("/quotes/{token}", response_model=QuotePublicOut)
def view_quote(token: str, db: Session = Depends(get_db)) -> QuotePublicOut:
    """The customer's quote page payload (token is the only credential)."""
    quote = quote_service.get_quote_by_token(db, token)
    request, _, company = _load_quote_context(db, quote)
    return QuotePublicOut(
        company_name=company.name,
        status=quote.status.value,
        currency=quote.currency,
        amount_min_cents=quote.amount_min_cents,
        amount_max_cents=quote.amount_max_cents,
        estimated_hours=quote.estimated_hours,
        crew_size=quote.crew_size,
        line_items=list(quote.line_items),
        valid_until=quote.valid_until,
        move_date=request.move_date,
        origin_city=request.origin_city,
        destination_city=request.destination_city,
        home_size=request.home_size.value,
    )


@router.post(
    "/quotes/{token}/accept",
    response_model=AcceptQuoteResponse,
    dependencies=[Depends(_limit_quote_action)],
)
def accept_quote(
    token: str,
    db: Session = Depends(get_db),
    email_provider: AnyEmailProvider = Depends(email_provider_dep),
) -> AcceptQuoteResponse:
    """Customer accepts the quote; a booking is created automatically."""
    quote = quote_service.get_quote_by_token(db, token)
    booking = quote_service.accept_quote(db, quote)
    _, lead, company = _load_quote_context(db, quote)
    notifications.send_acceptance_notifications(
        email_provider, quote=quote, booking=booking, lead=lead, company=company
    )
    return AcceptQuoteResponse(
        booking_id=booking.id,
        scheduled_date=booking.scheduled_date,
        crew_size=booking.crew_size,
        status=booking.status.value,
        company_name=company.name,
    )


@router.post(
    "/quotes/{token}/decline",
    response_model=QuotePublicOut,
    dependencies=[Depends(_limit_quote_action)],
)
def decline_quote(token: str, db: Session = Depends(get_db)) -> QuotePublicOut:
    """Customer declines the quote."""
    quote = quote_service.get_quote_by_token(db, token)
    quote_service.decline_quote(db, quote)
    return view_quote(token, db)
