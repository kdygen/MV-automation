"""Job model — completed moves with **actuals**: the ML training table.

Every completed booking (and every CSV-imported historical move) becomes a job row.
Move features are denormalized onto the row so training never needs a 4-table join and
imported jobs (which have no booking/request chain) carry the same shape. Quoted
values are kept alongside actuals so quote-vs-actual accuracy is a simple per-row
comparison — that error signal is what Stages 2–4 of the pricing engine learn from.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID
from app.models.moving_request import HomeSize, PackingService


class JobSource(enum.StrEnum):
    PLATFORM = "platform"  # completed through the product
    IMPORT = "import"  # historical CSV upload


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    source: Mapped[JobSource] = mapped_column(
        SAEnum(JobSource, native_enum=False, length=20, validate_strings=True),
        default=JobSource.PLATFORM,
        nullable=False,
    )

    # --- Denormalized move features (training inputs) ---
    move_date: Mapped[date] = mapped_column(Date, nullable=False)
    home_size: Mapped[HomeSize] = mapped_column(
        SAEnum(HomeSize, native_enum=False, length=20, validate_strings=True), nullable=False
    )
    packing_service: Mapped[PackingService] = mapped_column(
        SAEnum(PackingService, native_enum=False, length=20, validate_strings=True),
        default=PackingService.NONE,
        nullable=False,
    )
    distance_miles: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Quoted values (present for platform jobs; None for imports) ---
    quoted_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    quoted_total_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- Actuals (training targets) ---
    actual_hours: Mapped[float] = mapped_column(Float, nullable=False)
    actual_crew_size: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_volume_cuft: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Job {self.id} {self.home_size.value} actual={self.actual_hours}h>"
