"""Company availability model — per-date capacity overrides and blocks.

Deliberately sparse: a row exists only where a company *departs* from its default
(a blocked holiday, a day with an extra truck). The common case — an ordinary working
day at default capacity — needs no row at all, so this table stays small and an empty
table means "normal capacity every day" rather than "nothing is available".

Whether a date is bookable is **derived, never stored**: see
:func:`app.services.availability.availability_calendar`. Storing an ``is_available``
flag would immediately drift from the ``bookings`` table it is supposed to summarize.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID


class CompanyDateCapacity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One company's override for one calendar date."""

    __tablename__ = "company_date_capacity"
    __table_args__ = (
        UniqueConstraint("company_id", "date", name="uq_company_date_capacity_company_id_date"),
        # The only read pattern: this tenant's overrides across a date window.
        Index("ix_company_date_capacity_company_id_date", "company_id", "date"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    #: NULL means "use the company default"; 0 is a valid explicit "no crews today".
    max_moves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: A hard block regardless of capacity — holidays, maintenance, weather.
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Internal note for the owner. Never shown to customers.
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        state = "blocked" if self.is_blocked else f"max={self.max_moves}"
        return f"<CompanyDateCapacity {self.date} {state}>"
