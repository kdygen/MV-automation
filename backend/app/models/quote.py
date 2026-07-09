"""Quote model.

A quote freezes a priced offer: the customer-facing range, the line items, the engine
version, the exact pricing-config row, and a full input snapshot — enough to audit or
replay the price forever. ``public_token`` is the unguessable key in the customer's
quote link; it is the only way to reach a quote without authentication.

Lifecycle: ``draft`` (awaiting owner review, review-mode companies only) → ``sent`` →
``accepted`` | ``declined`` | ``expired``.
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

    public_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Quote {self.id} status={self.status.value}>"
