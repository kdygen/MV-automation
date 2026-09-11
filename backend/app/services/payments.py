"""Payment service: deposits, hosted checkout, and webhook-driven booking.

The lifecycle is **pay first, then confirm the booking**:

1. the customer chooses to book; the server computes the deposit from the quote,
2. a checkout session is created and a ``pending`` payment row recorded,
3. the customer pays on the provider's hosted page — no card data reaches us,
4. the provider's **signed webhook** marks the payment succeeded and only then calls
   :func:`app.services.quotes.accept_quote`, which creates the booking.

The browser's return trip proves nothing and changes nothing. A booking exists if and
only if a verified webhook said money arrived, which is why ``accept_quote`` did not
need to change: the webhook simply calls the same tested transition the direct accept
path already used.

Deposits are configuration, not policy invented here. A company with no deposit
configured keeps the original behaviour — accept books immediately, and no payment
provider is involved at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models import (
    Booking,
    Company,
    Payment,
    PaymentStatus,
    ProcessedWebhookEvent,
    Quote,
    QuoteStatus,
)
from app.providers.payment import PaymentEvent, PaymentProvider
from app.services import quotes as quote_service

logger = get_logger(__name__)

#: Deposit modes a company may configure in ``company.settings``.
DEPOSIT_NONE = "none"
DEPOSIT_FIXED = "fixed"
DEPOSIT_PERCENT = "percent"

#: Never ask for less than this once a deposit is configured — below it the provider's
#: own minimum charge makes the transaction pointless.
MIN_DEPOSIT_CENTS = 100


def deposit_cents(company: Company, quote: Quote) -> int:
    """How much this company collects up front for this quote. ``0`` means none.

    Percentages are taken against ``amount_min_cents`` — the bottom of the range the
    customer was shown — so the up-front ask is never more than the job's floor.
    """
    settings = company.settings or {}
    mode = str(settings.get("deposit_type", DEPOSIT_NONE)).lower()

    if mode == DEPOSIT_FIXED:
        amount = int(settings.get("deposit_amount_cents", 0))
    elif mode == DEPOSIT_PERCENT:
        percent = float(settings.get("deposit_percent", 0))
        amount = int(round(quote.amount_min_cents * percent / 100))
    else:
        return 0

    if amount < MIN_DEPOSIT_CENTS:
        return 0
    # Never ask for more than the low end of the customer's own quote.
    return min(amount, quote.amount_min_cents)


def requires_payment(company: Company, quote: Quote) -> bool:
    return deposit_cents(company, quote) > 0


def _company(db: Session, quote: Quote) -> Company:
    company = db.get(Company, quote.company_id)
    if company is None:  # pragma: no cover - FK guarantees it
        raise NotFoundError("Company not found")
    return company


def _assert_payable(quote: Quote) -> None:
    if quote.status is QuoteStatus.ACCEPTED:
        raise ConflictError("This quote has already been accepted")
    if quote.status is not QuoteStatus.SENT:
        raise ConflictError(f"This quote cannot be paid (status: {quote.status.value})")


def create_checkout(
    db: Session,
    quote: Quote,
    *,
    provider: PaymentProvider,
    settings: Settings,
    provider_name: str,
) -> tuple[Payment, str]:
    """Start a hosted checkout for this quote's deposit; returns the row and the URL.

    The amount is computed here from the quote and the company's configuration. It is
    never accepted from the caller, so no browser payload and no model output can
    influence what is charged.
    """
    company = _company(db, quote)
    _assert_payable(quote)

    amount = deposit_cents(company, quote)
    if amount <= 0:
        raise ConflictError("This company does not take a deposit online")

    base = settings.cors_origin_list[0] if settings.cors_origin_list else ""
    session = provider.create_checkout(
        amount_cents=amount,
        currency=quote.currency,
        description=f"Deposit for your move with {company.name}",
        success_url=base + settings.payment_success_path.format(token=quote.public_token),
        cancel_url=base + settings.payment_cancel_path.format(token=quote.public_token),
        # The provider only ever sees an opaque quote id, never a customer identity.
        reference=str(quote.id),
    )

    payment = Payment(
        company_id=quote.company_id,
        quote_id=quote.id,
        provider=provider_name,
        provider_session_id=session.session_id,
        amount_cents=amount,
        currency=quote.currency,
        status=PaymentStatus.PENDING,
    )
    db.add(payment)
    db.commit()
    logger.info(
        "Checkout created for quote %s: %d %s session=%s",
        quote.id,
        amount,
        quote.currency,
        session.session_id,
    )
    return payment, session.url


def latest_payment(db: Session, quote_id: uuid.UUID) -> Payment | None:
    return db.scalar(
        select(Payment).where(Payment.quote_id == quote_id).order_by(Payment.created_at.desc())
    )


def _claim_event(db: Session, provider_name: str, event: PaymentEvent) -> bool:
    """Record this event id, returning ``False`` if it was already processed.

    The unique constraint is the real guard: two concurrent deliveries both pass the
    SELECT, and exactly one survives the INSERT.
    """
    db.add(
        ProcessedWebhookEvent(
            provider=provider_name, event_id=event.event_id, event_type=event.kind
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info("Duplicate webhook event %s ignored", event.event_id)
        return False
    return True


def handle_event(
    db: Session, event: PaymentEvent, *, provider_name: str
) -> Booking | None:
    """Apply one verified provider event. Returns a booking if this call created one.

    Idempotent at three levels: the event id is claimed once, a payment already marked
    succeeded is not reprocessed, and ``accept_quote`` itself refuses a quote that is
    not still on offer.
    """
    if not _claim_event(db, provider_name, event):
        return None
    if event.session_id is None or event.kind == "ignored":
        return None

    payment = db.scalar(
        select(Payment).where(Payment.provider_session_id == event.session_id)
    )
    if payment is None:
        # A session we never created — another environment sharing the endpoint, or a
        # forged reference. Verified but unknown, so nothing to do.
        logger.warning("Webhook for unknown session %s", event.session_id)
        return None

    if event.kind in {"failed", "expired"}:
        if payment.status is PaymentStatus.PENDING:
            payment.status = (
                PaymentStatus.FAILED if event.kind == "failed" else PaymentStatus.CANCELLED
            )
            db.commit()
        return None

    if payment.status is PaymentStatus.SUCCEEDED:
        return None  # already applied by an earlier event for the same session

    payment.status = PaymentStatus.SUCCEEDED
    payment.succeeded_at = datetime.now(UTC)
    payment.provider_payment_intent_id = event.payment_intent_id
    db.commit()

    quote = db.get(Quote, payment.quote_id)
    if quote is None:  # pragma: no cover - FK guarantees it
        return None
    if quote.status is QuoteStatus.ACCEPTED:
        return db.scalar(select(Booking).where(Booking.quote_id == quote.id))

    # The same transition the no-deposit path uses — payment simply gates it.
    booking = quote_service.accept_quote(db, quote)
    logger.info("Payment %s confirmed; booking %s created", payment.id, booking.id)
    return booking
