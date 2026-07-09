"""Job service: booking completion, CSV import of historical jobs, accuracy stats.

This is the platform's data flywheel: every completed booking and every imported
historical move lands in ``jobs`` with the same denormalized shape, ready for the
retrieval (Stage 2) and ML (Stage 3) pricing engines. CSV import exists precisely to
solve the ML cold-start problem — a company can upload years of history on day one.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models import (
    Booking,
    BookingStatus,
    Job,
    JobSource,
    Lead,
    LeadStatus,
    MovingRequest,
    Quote,
)
from app.models.moving_request import HomeSize, PackingService
from app.schemas.jobs import (
    AccuracySummaryOut,
    CompleteBookingIn,
    ImportError_,
    ImportResultOut,
    JobOut,
)

logger = get_logger(__name__)

REQUIRED_CSV_COLUMNS = {
    "move_date",
    "home_size",
    "actual_hours",
    "actual_crew_size",
    "actual_total_dollars",
}
OPTIONAL_CSV_COLUMNS = {"packing_service", "distance_miles", "actual_volume_cuft"}


def complete_booking(
    db: Session, *, company_id: uuid.UUID, booking_id: uuid.UUID, payload: CompleteBookingIn
) -> Job:
    """Mark a booking completed and record the job actuals (one transaction)."""
    booking = db.scalar(
        select(Booking).where(Booking.id == booking_id, Booking.company_id == company_id)
    )
    if booking is None:
        raise NotFoundError("Booking not found")
    if booking.status is BookingStatus.COMPLETED:
        raise ConflictError("Booking is already completed")
    if booking.status is BookingStatus.CANCELLED:
        raise ConflictError("Cancelled bookings cannot be completed")

    quote = db.get(Quote, booking.quote_id)
    assert quote is not None  # FK guarantees it
    request = db.get(MovingRequest, quote.moving_request_id)
    assert request is not None
    lead = db.get(Lead, request.lead_id)

    booking.status = BookingStatus.COMPLETED
    if lead is not None:
        lead.status = LeadStatus.COMPLETED

    job = Job(
        company_id=company_id,
        booking_id=booking.id,
        source=JobSource.PLATFORM,
        move_date=booking.scheduled_date,
        home_size=request.home_size,
        packing_service=request.packing_service,
        distance_miles=request.distance_miles,
        quoted_hours=quote.estimated_hours,
        quoted_total_cents=quote.total_cents,
        actual_hours=payload.actual_hours,
        actual_crew_size=payload.actual_crew_size,
        actual_total_cents=round(payload.actual_total_dollars * 100),
        actual_volume_cuft=payload.actual_volume_cuft,
        notes=payload.notes,
    )
    db.add(job)
    db.commit()
    logger.info(
        "Booking %s completed: %.1fh actual vs %.1fh quoted",
        booking.id,
        payload.actual_hours,
        quote.estimated_hours,
    )
    return job


def list_jobs(db: Session, company_id: uuid.UUID) -> list[JobOut]:
    rows = db.scalars(
        select(Job).where(Job.company_id == company_id).order_by(Job.move_date.desc())
    )
    return [JobOut.model_validate(job) for job in rows]


def import_jobs_csv(db: Session, company_id: uuid.UUID, content: bytes) -> ImportResultOut:
    """Import historical jobs from CSV. Valid rows import; bad rows are reported.

    Required columns: move_date (YYYY-MM-DD), home_size, actual_hours,
    actual_crew_size, actual_total_dollars. Optional: packing_service,
    distance_miles, actual_volume_cuft.
    """
    try:
        text = content.decode("utf-8-sig")  # tolerate Excel's BOM
    except UnicodeDecodeError as exc:
        raise ValidationError("File must be UTF-8 encoded CSV") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValidationError("CSV file is empty")
    headers = {h.strip().lower() for h in reader.fieldnames}
    missing = REQUIRED_CSV_COLUMNS - headers
    if missing:
        raise ValidationError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

    imported = 0
    errors: list[ImportError_] = []
    jobs: list[Job] = []

    for row_number, raw in enumerate(reader, start=1):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            jobs.append(_job_from_csv_row(company_id, row))
            imported += 1
        except ValueError as exc:
            errors.append(ImportError_(row=row_number, message=str(exc)))

    db.add_all(jobs)
    db.commit()
    logger.info(
        "CSV import for company %s: %d imported, %d rejected", company_id, imported, len(errors)
    )
    return ImportResultOut(imported=imported, errors=errors)


def _job_from_csv_row(company_id: uuid.UUID, row: dict[str, str]) -> Job:
    try:
        move_date = date.fromisoformat(row["move_date"])
    except ValueError as exc:
        raise ValueError(f"move_date '{row['move_date']}' is not YYYY-MM-DD") from exc

    try:
        home_size = HomeSize(row["home_size"])
    except ValueError as exc:
        valid = ", ".join(size.value for size in HomeSize)
        raise ValueError(f"home_size '{row['home_size']}' is not one of: {valid}") from exc

    packing_raw = row.get("packing_service") or "none"
    try:
        packing = PackingService(packing_raw)
    except ValueError as exc:
        raise ValueError(f"packing_service '{packing_raw}' is not none/partial/full") from exc

    def positive_float(field: str) -> float:
        try:
            value = float(row[field])
        except (ValueError, KeyError) as exc:
            raise ValueError(f"{field} '{row.get(field, '')}' is not a number") from exc
        if value <= 0:
            raise ValueError(f"{field} must be positive")
        return value

    actual_hours = positive_float("actual_hours")
    actual_total = positive_float("actual_total_dollars")
    try:
        crew = int(row["actual_crew_size"])
    except ValueError as exc:
        raise ValueError(
            f"actual_crew_size '{row['actual_crew_size']}' is not an integer"
        ) from exc
    if not 1 <= crew <= 10:
        raise ValueError("actual_crew_size must be between 1 and 10")

    distance: float | None = None
    if row.get("distance_miles"):
        distance = positive_float("distance_miles")
    volume: float | None = None
    if row.get("actual_volume_cuft"):
        volume = positive_float("actual_volume_cuft")

    return Job(
        company_id=company_id,
        booking_id=None,
        source=JobSource.IMPORT,
        move_date=move_date,
        home_size=home_size,
        packing_service=packing,
        distance_miles=distance,
        quoted_hours=None,
        quoted_total_cents=None,
        actual_hours=actual_hours,
        actual_crew_size=crew,
        actual_total_cents=round(actual_total * 100),
        actual_volume_cuft=volume,
    )


def accuracy_summary(db: Session, company_id: uuid.UUID) -> AccuracySummaryOut:
    """Quote-vs-actual error stats over platform jobs (the engine's scorecard)."""
    jobs = list(db.scalars(select(Job).where(Job.company_id == company_id)))
    with_quote = [
        j for j in jobs if j.quoted_total_cents is not None and j.quoted_hours is not None
    ]

    total_mape: float | None = None
    hours_mae: float | None = None
    if with_quote:
        total_mape = (
            sum(
                abs(j.actual_total_cents - j.quoted_total_cents) / j.quoted_total_cents
                for j in with_quote
                if j.quoted_total_cents  # guarded non-zero by construction
            )
            / len(with_quote)
            * 100
        )
        hours_mae = sum(abs(j.actual_hours - (j.quoted_hours or 0)) for j in with_quote) / len(
            with_quote
        )

    return AccuracySummaryOut(
        job_count=len(jobs),
        jobs_with_quote=len(with_quote),
        total_mape_pct=round(total_mape, 2) if total_mape is not None else None,
        hours_mae=round(hours_mae, 2) if hours_mae is not None else None,
    )
