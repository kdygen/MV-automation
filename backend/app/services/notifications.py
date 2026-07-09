"""Notification service: composes and sends transactional email.

Plain-text templates for the MVP; HTML branding can layer on later without touching
callers. All functions are no-throw: a mail failure must never break a quote or
booking transaction — it is logged and the business flow continues.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.models import Booking, Company, Lead, Quote

logger = get_logger(__name__)


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _safe_send(provider: object, *, to: str, subject: str, text_body: str) -> None:
    try:
        provider.send(to=to, subject=subject, text_body=text_body)  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Email send failed (to=%s subject=%r) — continuing", to, subject)


def send_quote_to_customer(
    provider: object, *, quote: Quote, lead: Lead, company: Company, quote_url: str
) -> None:
    """The customer's quote link + range."""
    _safe_send(
        provider,
        to=lead.email,
        subject=f"Your moving quote from {company.name}",
        text_body=(
            f"Hi {lead.name},\n\n"
            f"Your estimated moving cost is {_dollars(quote.amount_min_cents)}"
            f"–{_dollars(quote.amount_max_cents)}.\n\n"
            f"View and accept your quote here:\n{quote_url}\n\n"
            f"This estimate is valid until {quote.valid_until.date().isoformat()}.\n\n"
            f"— {company.name}"
        ),
    )


def send_acceptance_notifications(
    provider: object, *, quote: Quote, booking: Booking, lead: Lead, company: Company
) -> None:
    """Confirmation to the customer + heads-up to the company."""
    _safe_send(
        provider,
        to=lead.email,
        subject=f"Booking confirmed — {company.name}",
        text_body=(
            f"Hi {lead.name},\n\n"
            f"Your move on {booking.scheduled_date.isoformat()} is booked. "
            f"A crew of {booking.crew_size} will handle it.\n\n"
            f"— {company.name}"
        ),
    )
    if company.email:
        _safe_send(
            provider,
            to=company.email,
            subject=f"New booking: {lead.name} on {booking.scheduled_date.isoformat()}",
            text_body=(
                f"{lead.name} ({lead.email}) accepted quote "
                f"{_dollars(quote.amount_min_cents)}–{_dollars(quote.amount_max_cents)} "
                f"and is booked for {booking.scheduled_date.isoformat()}."
            ),
        )


def send_review_needed_to_company(
    provider: object, *, lead: Lead, company: Company
) -> None:
    """Tell a review-mode company a draft quote is waiting for approval."""
    if company.email:
        _safe_send(
            provider,
            to=company.email,
            subject=f"Quote awaiting your review — {lead.name}",
            text_body=(
                f"A new moving request from {lead.name} ({lead.email}) has a draft "
                f"quote waiting for your approval in the dashboard."
            ),
        )
