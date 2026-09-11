"""Job model — completed moves with **actuals**: the historical intelligence table.

Every completed booking and every imported historical move becomes a job row. Features
are denormalized onto the row so analysis never needs a five-table join and imported
rows (which have no booking/request chain) carry the same shape. Quoted values sit
beside actuals so quote-vs-actual error is a per-row subtraction — the signal Stages
2-4 of the pricing engine will eventually learn from.

One table on purpose. A separate ``historical_moves`` would force every analytic, every
similarity query and the dashboard to UNION two shapes, and would mean copying platform
completions across; ``source`` already distinguishes provenance without that cost.

**Historical moves are evidence, not authority.** Nothing here feeds the pricing engine
directly. Analysis over these rows produces signals ("similar moves took 7.2h",
"estimates run 15% low"); whether a signal changes a price is a deliberate, separately
tested decision made elsewhere.

Almost every field is nullable because real exports are incomplete. The required
minimum is ``move_date``, ``home_size``, and at least one outcome — hours or total. A
row with neither outcome is not evidence, and the importer rejects it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID, JSONType
from app.models.moving_request import HomeSize, PackingService


class JobSource(enum.StrEnum):
    PLATFORM = "platform"  # completed through the product
    IMPORT = "import"  # historical upload (CSV/XLSX) or manual entry


class MoveType(enum.StrEnum):
    """Coarse move category. ``OTHER`` absorbs a company's own vocabulary.

    Deliberately coarse: an open string would let every tenant invent its own values and
    make cross-company analytics meaningless, while a long enum would reject real data.
    """

    LOCAL = "local"
    LONG_DISTANCE = "long_distance"
    COMMERCIAL = "commercial"
    STORAGE = "storage"
    OTHER = "other"


class ParkingDifficulty(enum.StrEnum):
    EASY = "easy"
    MODERATE = "moderate"
    DIFFICULT = "difficult"


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # Re-uploading an export must not duplicate a company's history. When the export
        # carries the company's own job number that is the identity; otherwise a content
        # hash stands in. NULLs do not collide in either PostgreSQL or SQLite, so
        # platform jobs (which have neither) are unaffected.
        UniqueConstraint("company_id", "external_ref", name="uq_jobs_company_id_external_ref"),
        UniqueConstraint("company_id", "row_hash", name="uq_jobs_company_id_row_hash"),
        # The history table's only ordering, and the similarity scan's filter.
        Index("ix_jobs_company_id_move_date", "company_id", "move_date"),
        # Building-level operational memory: "what happened at this address before?"
        Index("ix_jobs_company_id_origin_building_key", "company_id", "origin_building_key"),
    )

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

    # --- Quoted values (from our own quote for platform jobs; often present in
    # legacy exports too, which is what makes estimate-bias analysis possible) ---
    quoted_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    quoted_crew_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quoted_total_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- Actuals (analysis targets) ---
    # Nullable since Step 5: a legacy export may carry hours without money, or money
    # without hours. The importer still requires at least one of them per row.
    actual_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_crew_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_total_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actual_volume_cuft: Mapped[float | None] = mapped_column(Float, nullable=True)
    additional_charges_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- Geography -------------------------------------------------------------
    # City/state/ZIP are the default: enough for regional patterns without holding a
    # customer's home address. Street lines are opt-in (see below).
    origin_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    origin_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    origin_zip: Mapped[str | None] = mapped_column(String(10), nullable=True)
    destination_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    destination_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    destination_zip: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Street addresses are populated **only** when a company deliberately maps them
    # during import. They are the most sensitive field here and are excluded from every
    # customer-facing payload; they exist because building-level memory ("this address
    # needs a COI") is only actionable if a dispatcher can read the address.
    origin_line1: Mapped[str | None] = mapped_column(String(300), nullable=True)
    destination_line1: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: Normalized ``line1|zip``, derived on write. Groups repeat visits to one building
    #: without anyone having to match free-text addresses at query time.
    origin_building_key: Mapped[str | None] = mapped_column(String(320), nullable=True)
    destination_building_key: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # --- Access (the largest driver of actual hours after home size) -----------
    origin_floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_has_elevator: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    origin_stairs_flights: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination_floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination_has_elevator: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    destination_stairs_flights: Mapped[int | None] = mapped_column(Integer, nullable=True)
    long_carry: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    parking_difficulty: Mapped[ParkingDifficulty | None] = mapped_column(
        SAEnum(ParkingDifficulty, native_enum=False, length=20, validate_strings=True),
        nullable=True,
    )

    # --- Services and items ----------------------------------------------------
    move_type: Mapped[MoveType | None] = mapped_column(
        SAEnum(MoveType, native_enum=False, length=20, validate_strings=True), nullable=True
    )
    #: NULL means "we do not know"; ``[]`` means "we know there were none". The
    #: distinction matters to similarity, which must not treat unknown as absent.
    special_items: Mapped[list[Any] | None] = mapped_column(JSONType, nullable=True)
    has_storage: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- Outcome / operational intelligence ------------------------------------
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delay_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Short deterministic labels ("freight_elevator", "coi_required", "no_loading_zone").
    #: A structured path to the same insight embeddings would give, available today.
    issue_tags: Mapped[list[Any] | None] = mapped_column(JSONType, nullable=True)

    # Kept as separate columns rather than one blob: each is independently embeddable
    # later, and "what goes wrong at this building" is a different question from "why
    # did the price change". Concatenating them now would throw that away.
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    problem_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    building_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    variance_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # --- Provenance ------------------------------------------------------------
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("job_import_batches.id", ondelete="SET NULL"), nullable=True
    )
    #: The company's own identifier for the job in their old system, when the export has
    #: one. The strongest duplicate key available.
    external_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Content fingerprint used for duplicate detection when there is no external ref.
    row_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<Job {self.id} {self.home_size.value} "
            f"actual={self.actual_hours}h {self.source.value}>"
        )
