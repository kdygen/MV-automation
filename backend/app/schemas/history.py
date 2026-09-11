"""Schemas for historical-move import, manual entry, and the history views.

Three rules show up repeatedly in the shapes below:

* ``extra="forbid"`` everywhere, so a body carrying ``company_id`` is **rejected** rather
  than ignored. Combined with the absence of a canonical ``company_id`` field, a tenant
  cannot be addressed from a request at all.
* Mapping keys are validated against the canonical registry, so a client cannot invent a
  destination for a column.
* Customer-facing surfaces do not exist here. History is dashboard-only, and street
  addresses are returned solely on the detail view an authenticated owner opens.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.history.fields import FIELDS_BY_NAME, SENSITIVE_FIELDS
from app.history.parsing import DateOrder, DecimalStyle
from app.models.job import MoveType, ParkingDifficulty
from app.models.moving_request import HomeSize, PackingService


class ColumnInfo(BaseModel):
    """One column of the uploaded file, with a few values so a person can recognise it."""

    header: str
    sample_values: list[str] = Field(default_factory=list)
    suggested_field: str | None = None
    #: ``exact`` or ``partial`` — shown so a weak suggestion reads as a weak suggestion.
    confidence: str | None = None


class FieldInfo(BaseModel):
    """A canonical field offered in the mapping UI."""

    name: str
    label: str
    kind: str
    required: bool
    outcome: bool
    sensitive: bool


class AmbiguityOut(BaseModel):
    """What the company must decide before the import can be trusted."""

    date_order: str | None = None
    decimal_style: str | None = None
    date_ambiguous: bool = False
    money_ambiguous: bool = False
    ambiguous_money_fields: list[str] = Field(default_factory=list)
    needs_input: bool = False


class InspectOut(BaseModel):
    """Result of looking at an upload. Writes nothing."""

    filename: str
    file_format: str
    row_count: int
    columns: list[ColumnInfo]
    fields: list[FieldInfo]
    suggested_mapping: dict[str, str]
    ambiguity: AmbiguityOut


class RowIssueOut(BaseModel):
    field: str | None = None
    message: str


class RowVerdictOut(BaseModel):
    row_number: int
    status: str
    errors: list[RowIssueOut] = Field(default_factory=list)
    warnings: list[RowIssueOut] = Field(default_factory=list)
    #: A readable digest of what would be stored, for spot-checking the mapping.
    preview: dict[str, Any] = Field(default_factory=dict)


class PreviewOut(BaseModel):
    """Per-row verdicts plus totals. **Nothing has been written.**"""

    filename: str
    total: int
    importable: int
    warnings: int
    rejected: int
    duplicates: int
    ambiguity: AmbiguityOut
    #: Capped: a 10,000-row file should not return 10,000 rows of detail.
    rows: list[RowVerdictOut] = Field(default_factory=list)
    rows_truncated: bool = False


class ImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    file_format: str
    row_count_total: int
    row_count_imported: int
    row_count_skipped: int
    row_count_rejected: int
    reverted_at: datetime | None
    created_at: datetime


class ConfirmOut(BaseModel):
    """Result of an actual import."""

    batch: ImportBatchOut
    rejected_rows: list[RowVerdictOut] = Field(default_factory=list)


def _validate_mapping(mapping: dict[str, str]) -> dict[str, str]:
    """Reject any key that is not a canonical field.

    This is where a body claiming ``{"company_id": "Col A"}`` dies: the registry has no
    such field, so there is nowhere for the column to land.
    """
    unknown = sorted(set(mapping) - set(FIELDS_BY_NAME))
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(unknown)}")
    blank = sorted(name for name, header in mapping.items() if not str(header).strip())
    if blank:
        raise ValueError(f"Field(s) mapped to a blank column: {', '.join(blank)}")
    return {name: str(header) for name, header in mapping.items()}


class ImportRequest(BaseModel):
    """The mapping and parse choices that accompany a preview or a confirm."""

    model_config = ConfigDict(extra="forbid")

    mapping: dict[str, str]
    #: Supplied only when the file itself was ambiguous. An explicit answer, not a hint.
    date_order: DateOrder | None = None
    decimal_style: DecimalStyle | None = None

    @field_validator("mapping")
    @classmethod
    def _check_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_mapping(value)


class HistoricalMoveIn(BaseModel):
    """One hand-entered historical move.

    Typed at the edge, then run through the *same* business rules as an imported row —
    required fields, "at least one outcome", building-key derivation — so manual entry
    cannot become a way to store a move the importer would have rejected.
    """

    model_config = ConfigDict(extra="forbid")

    move_date: date
    home_size: HomeSize
    move_type: MoveType | None = None
    distance_miles: float | None = Field(default=None, ge=0, le=5000)

    origin_city: str | None = Field(default=None, max_length=120)
    origin_state: str | None = Field(default=None, min_length=2, max_length=2)
    origin_zip: str | None = Field(default=None, max_length=10)
    destination_city: str | None = Field(default=None, max_length=120)
    destination_state: str | None = Field(default=None, min_length=2, max_length=2)
    destination_zip: str | None = Field(default=None, max_length=10)
    origin_line1: str | None = Field(default=None, max_length=300)
    destination_line1: str | None = Field(default=None, max_length=300)

    origin_floor: int | None = Field(default=None, ge=0, le=100)
    origin_has_elevator: bool | None = None
    origin_stairs_flights: int | None = Field(default=None, ge=0, le=40)
    destination_floor: int | None = Field(default=None, ge=0, le=100)
    destination_has_elevator: bool | None = None
    destination_stairs_flights: int | None = Field(default=None, ge=0, le=40)
    long_carry: bool | None = None
    parking_difficulty: ParkingDifficulty | None = None

    packing_service: PackingService | None = None
    special_items: list[str] | None = Field(default=None, max_length=20)
    has_storage: bool | None = None

    quoted_hours: float | None = Field(default=None, ge=0, le=200)
    quoted_crew_size: int | None = Field(default=None, ge=1, le=20)
    quoted_total_dollars: float | None = Field(default=None, ge=0)
    actual_hours: float | None = Field(default=None, ge=0, le=200)
    actual_crew_size: int | None = Field(default=None, ge=1, le=20)
    actual_total_dollars: float | None = Field(default=None, ge=0)
    additional_charges_dollars: float | None = Field(default=None, ge=0)
    actual_volume_cuft: float | None = Field(default=None, ge=0, le=100_000)

    delay_minutes: int | None = Field(default=None, ge=0, le=2880)
    issue_tags: list[str] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)
    problem_notes: str | None = Field(default=None, max_length=2000)
    building_notes: str | None = Field(default=None, max_length=2000)
    change_notes: str | None = Field(default=None, max_length=2000)
    variance_reason: str | None = Field(default=None, max_length=300)
    external_ref: str | None = Field(default=None, max_length=120)


class HistoricalMovePatch(HistoricalMoveIn):
    """Same shape, everything optional — a correction to one stored move."""

    move_date: date | None = None  # type: ignore[assignment]
    home_size: HomeSize | None = None  # type: ignore[assignment]


class HistoryRowOut(BaseModel):
    """A row of the history table. No street addresses at list level."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    move_date: date
    home_size: str
    move_type: str | None
    origin_city: str | None
    origin_state: str | None
    destination_city: str | None
    destination_state: str | None
    distance_miles: float | None
    quoted_hours: float | None
    quoted_total_cents: int | None
    actual_hours: float | None
    actual_crew_size: int | None
    actual_total_cents: int | None


class HistoryDetailOut(HistoryRowOut):
    """Everything an owner may see about one historical move, addresses included."""

    packing_service: str
    special_items: list[Any] | None
    has_storage: bool | None
    origin_zip: str | None
    destination_zip: str | None
    origin_line1: str | None
    destination_line1: str | None
    origin_floor: int | None
    origin_has_elevator: bool | None
    origin_stairs_flights: int | None
    destination_floor: int | None
    destination_has_elevator: bool | None
    destination_stairs_flights: int | None
    long_carry: bool | None
    parking_difficulty: str | None
    quoted_crew_size: int | None
    additional_charges_cents: int | None
    actual_volume_cuft: float | None
    delay_minutes: int | None
    issue_tags: list[Any] | None
    notes: str | None
    problem_notes: str | None
    building_notes: str | None
    change_notes: str | None
    variance_reason: str | None
    external_ref: str | None
    import_batch_id: uuid.UUID | None
    created_at: datetime


class HistorySummaryOut(BaseModel):
    """The numbers that tell an owner whether their import landed correctly."""

    total_moves: int
    imported_moves: int
    platform_moves: int
    earliest_move_date: date | None = None
    latest_move_date: date | None = None
    median_actual_hours: float | None = None
    #: Signed: positive means estimates ran low. Reported, never applied.
    median_hours_error_pct: float | None = None
    median_price_error_pct: float | None = None
    moves_with_hours: int = 0
    moves_with_estimate: int = 0


SENSITIVE_FIELD_NAMES = sorted(SENSITIVE_FIELDS)


class SimilarQueryIn(BaseModel):
    """Features of the move we want comparable history for.

    Deliberately has no price field and no crew field: those are the answers we may one
    day want from this, so accepting them as inputs would make the reasoning circular.
    """

    model_config = ConfigDict(extra="forbid")

    home_size: HomeSize | None = None
    distance_miles: float | None = Field(default=None, ge=0, le=5000)
    packing_service: PackingService | None = None
    special_items: list[str] | None = Field(default=None, max_length=20)
    origin_floor: int | None = Field(default=None, ge=0, le=100)
    destination_floor: int | None = Field(default=None, ge=0, le=100)
    origin_stairs_flights: int | None = Field(default=None, ge=0, le=40)
    destination_stairs_flights: int | None = Field(default=None, ge=0, le=40)
    origin_has_elevator: bool | None = None
    destination_has_elevator: bool | None = None
    origin_zip: str | None = Field(default=None, max_length=10)
    destination_zip: str | None = Field(default=None, max_length=10)
    origin_city: str | None = Field(default=None, max_length=120)
    destination_city: str | None = Field(default=None, max_length=120)
    origin_state: str | None = Field(default=None, min_length=2, max_length=2)
    destination_state: str | None = Field(default=None, min_length=2, max_length=2)
    #: Exclude one move from its own comparables — used by "find similar to this".
    exclude_move_id: uuid.UUID | None = None
    limit: int = Field(default=10, ge=1, le=50)


class SimilarMoveOut(BaseModel):
    """One comparable historical move, with why it matched and how it turned out."""

    id: uuid.UUID
    source: str
    score: float
    coverage: float
    #: Feature names that agreed closely — the readable "why", not a black box.
    matched_on: list[str] = Field(default_factory=list)

    move_date: date
    home_size: str
    distance_miles: float | None = None
    packing_service: str | None = None
    special_items: list[Any] | None = None

    origin_floor: int | None = None
    origin_stairs_flights: int | None = None
    origin_has_elevator: bool | None = None
    destination_floor: int | None = None
    destination_stairs_flights: int | None = None
    destination_has_elevator: bool | None = None
    long_carry: bool | None = None
    parking_difficulty: str | None = None

    actual_hours: float | None = None
    actual_crew_size: int | None = None
    quoted_hours: float | None = None
    #: Signed: positive means the estimate ran low for this move.
    hours_error_pct: float | None = None

    delay_minutes: int | None = None
    issue_tags: list[Any] | None = None
    problem_notes: str | None = None
    building_notes: str | None = None


class HistoricalSignalsOut(BaseModel):
    """Evidence drawn from comparable history. **Not a price and not a decision.**

    Everything here is an observation about moves that already happened. There is
    deliberately no recommended price, no recommended crew, and no aggregate money
    figure: this endpoint exists to inform a human and, later, a separately tested
    calibration layer. The deterministic pricing engine remains the only thing that
    prices a quote.
    """

    comparable_count: int
    #: How many of the comparables actually recorded hours — the basis for the stats.
    moves_with_hours: int
    moves_with_estimate: int

    median_actual_hours: float | None = None
    mean_actual_hours: float | None = None
    #: Robust spread: the middle half of comparable durations.
    hours_p25: float | None = None
    hours_p75: float | None = None
    #: Signed median estimate error across comparables. Positive means estimates for
    #: moves like this have tended to run low.
    median_hours_error_pct: float | None = None
    #: Share of comparables where the job ran longer than estimated.
    overrun_share_pct: float | None = None
    #: Issue tags seen across comparables, most frequent first — operational memory.
    common_issue_tags: list[str] = Field(default_factory=list)

    matches: list[SimilarMoveOut] = Field(default_factory=list)
