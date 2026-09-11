"""Schemas for job completion, listing, CSV import, and accuracy reporting."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CompleteBookingIn(BaseModel):
    """Actuals captured when a crew finishes a move."""

    actual_hours: float = Field(gt=0, le=24)
    actual_crew_size: int = Field(ge=1, le=10)
    actual_total_dollars: float = Field(gt=0)
    actual_volume_cuft: float | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=2000)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    move_date: date
    home_size: str
    packing_service: str
    distance_miles: float | None
    quoted_hours: float | None
    quoted_total_cents: int | None
    # Nullable since Step 5: an imported historical move may carry hours without money
    # or money without hours. The importer guarantees at least one of them.
    actual_hours: float | None
    actual_crew_size: int | None
    actual_total_cents: int | None
    actual_volume_cuft: float | None
    created_at: datetime


class ImportError_(BaseModel):
    row: int  # 1-based data row number (excluding header)
    message: str


class ImportResultOut(BaseModel):
    imported: int
    errors: list[ImportError_]


class AccuracySummaryOut(BaseModel):
    """How well quotes have matched reality so far — the Stage 2+ scorecard."""

    job_count: int
    jobs_with_quote: int
    # Mean absolute percentage error of quoted total vs actual total (None until data).
    total_mape_pct: float | None
    # Mean absolute error of quoted hours vs actual hours (None until data).
    hours_mae: float | None
