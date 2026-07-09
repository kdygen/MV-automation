"""Booking model.

Created automatically when a customer accepts a quote. One booking per quote. Completing
a booking (Milestone 7) captures the actual hours/cost and becomes a training job for
the pricing engine's ML stages.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID


class BookingStatus(enum.StrEnum):
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Booking(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bookings"

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("quotes.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    time_window: Mapped[str | None] = mapped_column(String(50), nullable=True)
    crew_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        SAEnum(BookingStatus, native_enum=False, length=20, validate_strings=True),
        default=BookingStatus.CONFIRMED,
        nullable=False,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Booking {self.id} on {self.scheduled_date} status={self.status.value}>"
