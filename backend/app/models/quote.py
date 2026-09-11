"""Quote model.

A quote freezes a priced offer: the customer-facing range, the line items, the engine
version, the exact pricing-config row, and a full input snapshot — enough to audit or
replay the price forever. ``public_token`` is the unguessable key in the customer's
quote link; it is the only way to reach a quote without authentication.

Lifecycle: ``draft`` (awaiting owner review, review-mode companies only) → ``sent`` →
``accepted`` | ``declined`` | ``expired`` | ``superseded``.

Quotes are append-only. A customer edit never rewrites a quote: it creates the next
revision and points the old row at it through ``superseded_by_quote_id``. Every
revision therefore keeps its own untouched ``inputs_snapshot``, ``engine_version`` and
``pricing_config_id``, so any price this customer was ever shown can still be replayed
exactly. The customer's original link keeps working because token lookup follows the
chain forward to the current head.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID, JSONType


class QuoteStatus(enum.StrEnum):
    DRAFT = "draft"  # awaiting owner review (review-mode companies)
    SENT = "sent"  # visible to the customer, awaiting decision
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"  # replaced by a later revision after a customer edit


class Quote(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "quotes"

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    moving_request_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("moving_requests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    pricing_config_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("pricing_configs.id", ondelete="RESTRICT"), nullable=False
    )

    status: Mapped[QuoteStatus] = mapped_column(
        SAEnum(QuoteStatus, native_enum=False, length=20, validate_strings=True),
        default=QuoteStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # Money in integer cents; the customer sees the min–max range.
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    amount_min_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_max_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estimated_hours: Mapped[float] = mapped_column(Float, nullable=False)
    crew_size: Mapped[int] = mapped_column(Integer, nullable=False)

    # Full audit trail of how the price came to be.
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    line_items: Mapped[list[Any]] = mapped_column(JSONType, default=list, nullable=False)
    inputs_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    is_adjusted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: 1-based position in this quote's revision chain.
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: Set on the older row when a customer edit produces a new revision. NULL marks
    #: the head — the quote a token lookup ultimately resolves to.
    superseded_by_quote_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True
    )

    public_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Quote {self.id} r{self.revision} status={self.status.value}>"
