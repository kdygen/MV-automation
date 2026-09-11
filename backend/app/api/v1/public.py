"""Public (unauthenticated) endpoints for the customer-facing funnel.

These are scoped by the company's public slug or an unguessable quote token, and will
sit behind rate limiting at the deployment milestone. No tenant data is ever returned
beyond what belongs to the submitting customer's own request/quote.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    AnyEmailProvider,
    AnyPaymentProvider,
    distance_provider_dep,
    email_provider_dep,
    payment_provider_dep,
    quote_public_url,
)
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.core.ratelimit import rate_limited
from app.db.session import get_db
from app.models import Booking, ChangeKind, Company, Lead, MovingRequest, Quote, QuoteStatus
from app.pricing import PricingInputError
from app.providers.distance import DistanceProvider
from app.schemas.availability import AvailabilityOut, DayAvailabilityOut
from app.schemas.intake import IntakeResponse, MovingRequestIn, MovingRequestOut
from app.schemas.quotes import (
    AcceptQuoteResponse,
    CheckoutOut,
    PaymentStatusOut,
    QuotePublicOut,
    QuoteSummaryPublic,
)
from app.schemas.requote import ChangeDateIn, ChangePreviewOut, EditMoveIn, MoveDetailsOut
from app.services import availability as availability_service
from app.services import intake as intake_service
from app.services import notifications
from app.services import payments as payment_service
from app.services import quotes as quote_service
from app.services import requote as requote_service

logger = get_logger(__name__)

router = APIRouter(prefix="/public", tags=["public"])

# Public POSTs are internet-exposed with no auth; per-IP sliding-window limits damp
# abuse (form spam, token brute-forcing) without touching legitimate customers.
_limit_submit = rate_limited("public_submit", max_requests=20, window_seconds=600)
_limit_quote_action = rate_limited("public_quote_action", max_requests=30, window_seconds=600)
# Reads are cheap but a calendar walk is not free; a looser limit than writes.
_limit_quote_read = rate_limited("public_quote_read", max_requests=60, window_seconds=600)

#: Default picker window when the caller does not ask for one.
DEFAULT_CALENDAR_DAYS = 60


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


def _quote_page_payload(db: Session, quote: Quote) -> QuotePublicOut:
    """The customer-facing view of one quote. Shared by every endpoint that returns it."""
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
        revision=quote.revision,
        deposit_cents=payment_service.deposit_cents(company, quote),
    )


@router.get("/quotes/{token}", response_model=QuotePublicOut)
def view_quote(token: str, db: Session = Depends(get_db)) -> QuotePublicOut:
    """The customer's quote page payload (token is the only credential)."""
    return _quote_page_payload(db, quote_service.get_quote_by_token(db, token))


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
    """Customer accepts the quote; a booking is created automatically.

    Only for companies that take no deposit. When one is configured this refuses and
    points at checkout — otherwise the endpoint would be a way to book without paying.
    """
    quote = quote_service.get_quote_by_token(db, token)
    _, _, company = _load_quote_context(db, quote)
    if payment_service.requires_payment(company, quote):
        raise ConflictError("This move requires a deposit — please continue to payment")

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
    return _quote_page_payload(db, quote)


@router.get(
    "/quotes/{token}/availability",
    response_model=AvailabilityOut,
    dependencies=[Depends(_limit_quote_read)],
)
def quote_availability(
    token: str,
    start: date_type | None = Query(default=None, alias="from"),
    end: date_type | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
) -> AvailabilityOut:
    """Which dates this company can take the customer's move on.

    Scoped by the quote token like every other public endpoint: the window belongs to
    the quote's company, and load figures are deliberately not exposed — the customer
    learns that a date is unavailable, never how busy the company is.
    """
    quote = quote_service.get_quote_by_token(db, token)
    request, _, company = _load_quote_context(db, quote)

    today = date_type.today()
    start = start or today
    end = end or (start + timedelta(days=DEFAULT_CALENDAR_DAYS))

    days = availability_service.availability_calendar(
        db, company, start=start, end=end, today=today
    )
    first, last = availability_service.bookable_window(company, today)
    return AvailabilityOut(
        days=[
            DayAvailabilityOut(date=d.date, is_available=d.is_available, reason=d.reason)
            for d in days
        ],
        current_move_date=request.move_date,
        first_bookable_date=first,
        last_bookable_date=last,
    )


@router.post(
    "/quotes/{token}/date-preview",
    response_model=ChangePreviewOut,
    dependencies=[Depends(_limit_quote_action)],
)
def preview_date_change(
    token: str,
    payload: ChangeDateIn,
    db: Session = Depends(get_db),
) -> ChangePreviewOut:
    """Show what moving to another date would cost. **Writes nothing.**

    The customer must see this and confirm separately before anything changes, so a
    closed tab or a second thought leaves the quote exactly as it was.
    """
    quote = quote_service.get_quote_by_token(db, token)
    return requote_service.preview_changes(
        db, quote, requote_service.RequestChanges(move_date=payload.move_date)
    )


@router.post(
    "/quotes/{token}/date-change",
    response_model=QuotePublicOut,
    dependencies=[Depends(_limit_quote_action)],
)
def confirm_date_change(
    token: str,
    payload: ChangeDateIn,
    db: Session = Depends(get_db),
) -> QuotePublicOut:
    """Apply a date change the customer has confirmed, as a new quote revision.

    Every rule is re-validated here independently of the preview — availability
    especially, since a date can fill up between being offered and being chosen.
    """
    quote = quote_service.get_quote_by_token(db, token)
    new_quote = requote_service.confirm_changes(
        db,
        quote,
        requote_service.RequestChanges(move_date=payload.move_date),
        kind=ChangeKind.DATE,
    )
    return _quote_page_payload(db, new_quote)


@router.get(
    "/quotes/{token}/move-details",
    response_model=MoveDetailsOut,
    dependencies=[Depends(_limit_quote_read)],
)
def move_details(token: str, db: Session = Depends(get_db)) -> MoveDetailsOut:
    """The priced inputs behind this quote, as the edit form loads them.

    Cities are included so the customer can see which move they are editing; street
    addresses are not, matching the quote page's existing surface.
    """
    quote = quote_service.get_quote_by_token(db, token)
    request, _, _ = _load_quote_context(db, quote)
    return requote_service.move_details(request)


@router.post(
    "/quotes/{token}/edit-preview",
    response_model=ChangePreviewOut,
    dependencies=[Depends(_limit_quote_action)],
)
def preview_edit(
    token: str,
    payload: EditMoveIn,
    db: Session = Depends(get_db),
) -> ChangePreviewOut:
    """Reprice the move with the customer's edits applied. **Writes nothing.**"""
    quote = quote_service.get_quote_by_token(db, token)
    return requote_service.preview_changes(
        db, quote, requote_service.RequestChanges.from_edit(payload)
    )


@router.post(
    "/quotes/{token}/edit",
    response_model=QuotePublicOut,
    dependencies=[Depends(_limit_quote_action)],
)
def confirm_edit(
    token: str,
    payload: EditMoveIn,
    db: Session = Depends(get_db),
) -> QuotePublicOut:
    """Apply edits the customer has confirmed, as a new quote revision.

    Runs through the same validation and the same deterministic pricing engine as the
    original quote — a customer edit can never reach a price the engine would not have
    produced for those inputs.
    """
    quote = quote_service.get_quote_by_token(db, token)
    changes = requote_service.RequestChanges.from_edit(payload)
    kind = (
        ChangeKind.DATE
        if set(changes.applied()) == {"move_date"}
        else ChangeKind.DETAILS
    )
    new_quote = requote_service.confirm_changes(db, quote, changes, kind=kind)
    return _quote_page_payload(db, new_quote)


@router.post(
    "/quotes/{token}/checkout",
    response_model=CheckoutOut,
    dependencies=[Depends(_limit_quote_action)],
)
def start_checkout(
    token: str,
    db: Session = Depends(get_db),
    provider: AnyPaymentProvider = Depends(payment_provider_dep),
    settings: Settings = Depends(get_settings),
) -> CheckoutOut:
    """Begin hosted checkout for this quote's deposit.

    The amount is computed server-side from the quote and the company's configuration.
    The request body is empty by design: there is nothing a browser could send that
    would be trusted.
    """
    quote = quote_service.get_quote_by_token(db, token)
    payment, url = payment_service.create_checkout(
        db,
        quote,
        provider=provider,
        settings=settings,
        provider_name=settings.payment_provider.lower(),
    )
    return CheckoutOut(
        checkout_url=url, amount_cents=payment.amount_cents, currency=payment.currency
    )


@router.get(
    "/quotes/{token}/payment-status",
    response_model=PaymentStatusOut,
    dependencies=[Depends(_limit_quote_read)],
)
def payment_status(token: str, db: Session = Depends(get_db)) -> PaymentStatusOut:
    """What the return page polls while waiting for the webhook.

    ``booking_confirmed`` reflects a booking row that only a verified webhook creates,
    so a customer who lands here without paying sees ``false`` however they arrived.
    """
    quote = quote_service.get_quote_by_token(db, token)
    payment = payment_service.latest_payment(db, quote.id)
    booking = db.scalar(select(Booking).where(Booking.quote_id == quote.id))
    return PaymentStatusOut(
        status=payment.status.value if payment else "none",
        booking_confirmed=booking is not None,
        amount_cents=payment.amount_cents if payment else None,
        currency=payment.currency if payment else None,
    )
