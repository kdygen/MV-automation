"""Cached ZIP-to-ZIP driving distances.

Legacy exports routinely carry postcodes but not mileage, and the pricing engine cannot
price a move without a distance. Rather than guess, we resolve each distinct ZIP pair
once through the configured distance provider and cache the answer here.

**This table is deliberately not tenant-scoped, and that is not a cross-tenant leak.**
It holds a property of the road network — how far 62701 is from 62629 — which contains
no company data and reveals nothing about any company's moves. Sharing it avoids paying
a provider twice for the same physical fact. Nothing a calibration model learns ever
comes from here; only geometry does.
"""

from __future__ import annotations

from sqlalchemy import Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ZipDistance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One resolved origin→destination ZIP pair."""

    __tablename__ = "zip_distances"
    __table_args__ = (
        UniqueConstraint("origin_zip", "destination_zip", name="uq_zip_distances_pair"),
        Index("ix_zip_distances_pair", "origin_zip", "destination_zip"),
    )

    origin_zip: Mapped[str] = mapped_column(String(10), nullable=False)
    destination_zip: Mapped[str] = mapped_column(String(10), nullable=False)
    #: NULL records a *failed* lookup so we do not retry a hopeless pair on every
    #: training run. Absence of a row means "never asked"; a NULL means "asked, no answer".
    miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ZipDistance {self.origin_zip}->{self.destination_zip} {self.miles}mi>"
