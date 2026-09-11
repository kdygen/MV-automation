"""Quote service: create, review, send, accept, decline, expire; booking creation.

State machine (enforced here, nowhere else):

    draft ──approve──▶ sent ──accept──▶ accepted (+ booking, one per quote)
                        │──decline──▶ declined
                        │──(valid_until passes)──▶ expired

Instant-quote policy: companies quote automatically unless their settings carry
``quote_review_mode: true``, in which case quotes start as drafts and an owner approves
(optionally adjusting the range) before the customer sees a price.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models import (
    Booking,
    BookingStatus,
    Company,
    Lead,
    LeadStatus,
    MovingRequest,
    Quote,
    QuoteStatus,
    RequestStatus,
)
from app.services import pricing as pricing_service

logger = get_logger(__name__)

DEFAULT_QUOTE_VALIDITY_DAYS = 14


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize DB datetimes (SQLite returns them naive) to aware UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def company_review_mode(company: Company) -> bool:
    return bool(company.settings.get("quote_review_mode", False))


def company_validity_days(company: Company) -> int:
    return int(company.settings.get("quote_validity_days", DEFAULT_QUOTE_VALIDITY_DAYS))


def create_quote_for_request(
    db: Session, *, company: Company, request: MovingRequest, lead: Lead
) -> Quote:
    """Price the request and persist a quote (draft in review mode, else sent).

    :raises app.pricing.PricingInputError: when the request cannot be priced yet;
        the caller leaves the request pending for manual handling.
    """
    estimate, config_row = pricing_service.estimate_for_request(db, request)
    review = company_review_mode(company)

    quote = Quote(
        company_id=company.id,
        moving_request_id=request.id,
        pricing_config_id=config_row.id,
        status=QuoteStatus.DRAFT if review else QuoteStatus.SENT,
        currency=estimate.currency,
        amount_min_cents=estimate.amount_min_cents,
        amount_max_cents=estimate.amount_max_cents,
        total_cents=estimate.total_cents,
        estimated_hours=estimate.estimated_hours,
        crew_size=estimate.crew_size,
        engine_version=estimate.engine_version,
        line_items=[item.model_dump(mode="json") for item in estimate.line_items],
        inputs_snapshot=estimate.inputs,
        public_token=secrets.token_urlsafe(24),
        valid_until=_utcnow() + timedelta(days=company_validity_days(company)),
    )
    request.status = RequestStatus.QUOTED
    lead.status = LeadStatus.QUOTED
    db.add(quote)
    db.commit()

    logger.info(
        "Quote %s created (%s) for request %s: %s–%s cents",
        quote.id,
        quote.status.value,
        request.id,
        quote.amount_min_cents,
        quote.amount_max_cents,
    )
    return quote


def get_quote_by_token(db: Session, token: str) -> Quote:
    """Resolve a customer quote link to the current revision; lazily expires it.

    A token stays with the revision it was issued for, so after a customer edits their
    move the original emailed link still works — it resolves forward through
    ``superseded_by_quote_id`` to the head. Expiry is then evaluated on the head, which
    is the only revision still on offer.
    """
    quote = db.scalar(select(Quote).where(Quote.public_token == token))
    if quote is None:
        raise NotFoundError("Quote not found")

    # Imported here rather than at module scope: requote depends on this module, and a
    # top-level import would make the cycle real.
    from app.services.requote import resolve_head

    quote = resolve_head(db, quote)
    _expire_if_overdue(db, quote)
    return quote


def get_quote_for_company(db: Session, *, company_id: uuid.UUID, quote_id: uuid.UUID) -> Quote:
    """Tenant-scoped quote lookup for dashboard actions."""
    quote = db.scalar(
        select(Quote).where(Quote.id == quote_id, Quote.company_id == company_id)
    )
    if quote is None:
        raise NotFoundError("Quote not found")
    return quote


def approve_quote(
    db: Session,
    quote: Quote,
    *,
    amount_min_cents: int | None = None,
    amount_max_cents: int | None = None,
) -> Quote:
    """Owner approves a draft (optionally adjusting the range): draft → sent."""
    if quote.status is not QuoteStatus.DRAFT:
        raise ConflictError(f"Only draft quotes can be approved (status: {quote.status.value})")

    if amount_min_cents is not None or amount_max_cents is not None:
        new_min = amount_min_cents if amount_min_cents is not None else quote.amount_min_cents
        new_max = amount_max_cents if amount_max_cents is not None else quote.amount_max_cents
        if new_min <= 0 or new_max < new_min:
            raise ConflictError("Adjusted range is invalid (need 0 < min <= max)")
        quote.amount_min_cents = new_min
        quote.amount_max_cents = new_max
        quote.is_adjusted = True

    quote.status = QuoteStatus.SENT
    db.commit()
    logger.info("Quote %s approved%s", quote.id, " (adjusted)" if quote.is_adjusted else "")
    return quote


def accept_quote(db: Session, quote: Quote) -> Booking:
    """Customer accepts a sent quote: creates the booking, advances lead status."""
    _expire_if_overdue(db, quote)
    if quote.status is QuoteStatus.EXPIRED:
        raise ConflictError("This quote has expired; please request a new one")
    if quote.status is not QuoteStatus.SENT:
        raise ConflictError(f"Quote cannot be accepted (status: {quote.status.value})")

    request = db.get(MovingRequest, quote.moving_request_id)
    assert request is not None  # FK guarantees it
    lead = db.get(Lead, request.lead_id)
    assert lead is not None

    quote.status = QuoteStatus.ACCEPTED
    quote.accepted_at = _utcnow()
    lead.status = LeadStatus.BOOKED

    booking = Booking(
        company_id=quote.company_id,
        quote_id=quote.id,
        scheduled_date=request.move_date,
        crew_size=quote.crew_size,
        status=BookingStatus.CONFIRMED,
    )
    db.add(booking)
    db.commit()
    logger.info("Quote %s accepted; booking %s on %s", quote.id, booking.id, request.move_date)
    return booking


def decline_quote(db: Session, quote: Quote) -> Quote:
    """Customer declines a sent quote."""
    _expire_if_overdue(db, quote)
    if quote.status is not QuoteStatus.SENT:
        raise ConflictError(f"Quote cannot be declined (status: {quote.status.value})")

    request = db.get(MovingRequest, quote.moving_request_id)
    assert request is not None
    lead = db.get(Lead, request.lead_id)
    assert lead is not None

    quote.status = QuoteStatus.DECLINED
    lead.status = LeadStatus.LOST
    db.commit()
    return quote


def _expire_if_overdue(db: Session, quote: Quote) -> None:
    if quote.status is QuoteStatus.SENT and _as_utc(quote.valid_until) < _utcnow():
        quote.status = QuoteStatus.EXPIRED
        db.commit()
        logger.info("Quote %s expired (valid_until=%s)", quote.id, quote.valid_until)
