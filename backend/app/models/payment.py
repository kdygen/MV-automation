"""Payment records and webhook idempotency.

Two tables with one job each:

``payments`` is our own record of a checkout attempt. ``amount_cents`` is written when
the session is created, from the quote — so what we intended to collect is auditable
independently of anything the provider or the browser later says.

``processed_webhook_events`` is the duplicate-delivery guard. Providers retry webhooks,
sometimes for hours, and may deliver the same event more than once even after a 200.
Recording the provider's event id under a unique constraint makes a second delivery a
no-op rather than a second booking.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID


class PaymentStatus(enum.StrEnum):
    PENDING = "pending"  # session created, customer has not finished
    SUCCEEDED = "succeeded"  # provider confirmed funds — the only state that books
    FAILED = "failed"
    CANCELLED = "cancelled"  # session expired or was abandoned


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One checkout attempt against one quote."""

    __tablename__ = "payments"
    __table_args__ = (
        # The provider's session id is our idempotency key for a checkout attempt.
        UniqueConstraint("provider_session_id", name="uq_payments_provider_session_id"),
        Index("ix_payments_quote_id", "quote_id"),
        Index("ix_payments_company_id", "company_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: What we asked the customer to pay, computed server-side from the quote.
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, native_enum=False, length=20, validate_strings=True),
        default=PaymentStatus.PENDING,
        nullable=False,
        index=True,
    )
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Payment {self.amount_cents} {self.currency} status={self.status.value}>"


class ProcessedWebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One provider event we have already acted on."""

    __tablename__ = "processed_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "provider", "event_id", name="uq_processed_webhook_events_provider_event_id"
        ),
    )

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ProcessedWebhookEvent {self.provider}:{self.event_id}>"
