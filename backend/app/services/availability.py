"""Availability service: which dates a company can actually take a move on.

Deterministic and company-specific by construction. Nothing here consults a model, and
nothing here is stored as a boolean — availability is computed from three sources that
are each authoritative on their own:

* ``bookings``            — how many jobs are already committed on that date
* ``company_date_capacity`` — the owner's blocks and per-date overrides
* ``company.settings``    — the tenant's defaults (capacity, notice, horizon)

Storing an ``is_available`` flag would drift from ``bookings`` the moment a booking is
created or cancelled, so the flag is derived on every read instead.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import Booking, BookingStatus, Company, CompanyDateCapacity
from app.schemas.availability import DateCapacityIn, OwnerDayOut

#: Moves a company can run per day before a date is full. Two is a deliberately
#: conservative default for a small mover with one or two crews; owners override it.
DEFAULT_DAILY_CAPACITY = 2

#: How much notice a move needs. Same-day moves are not offered by default.
DEFAULT_LEAD_TIME_DAYS = 2

#: How far ahead a customer may book. Beyond this, pricing assumptions get unreliable.
DEFAULT_BOOKING_HORIZON_DAYS = 120

#: Widest window a single calendar request may ask for — roughly three months. Bounds
#: the work one public request can trigger.
MAX_CALENDAR_DAYS = 92

#: Why a date is not offered. Coarse on purpose: the customer learns that a date is
#: unavailable, never how many jobs the company already has that day.
REASON_BLOCKED = "unavailable"
REASON_FULL = "fully_booked"
REASON_TOO_SOON = "too_soon"
REASON_TOO_FAR = "too_far"


@dataclass(frozen=True)
class DayAvailability:
    """One calendar day as the customer's date picker sees it."""

    date: date_type
    is_available: bool
    reason: str | None = None


def company_daily_capacity(company: Company) -> int:
    return max(0, int(company.settings.get("daily_capacity", DEFAULT_DAILY_CAPACITY)))


def company_lead_time_days(company: Company) -> int:
    return max(0, int(company.settings.get("lead_time_days", DEFAULT_LEAD_TIME_DAYS)))


def company_booking_horizon_days(company: Company) -> int:
    return max(1, int(company.settings.get("booking_horizon_days", DEFAULT_BOOKING_HORIZON_DAYS)))


def bookable_window(company: Company, today: date_type) -> tuple[date_type, date_type]:
    """The inclusive first and last date this company can be booked for."""
    return (
        today + timedelta(days=company_lead_time_days(company)),
        today + timedelta(days=company_booking_horizon_days(company)),
    )


def _booked_counts(
    db: Session, company_id: uuid.UUID, start: date_type, end: date_type
) -> dict[date_type, int]:
    """Committed moves per date. Cancelled bookings free their slot back up."""
    rows = db.execute(
        select(Booking.scheduled_date, func.count())
        .where(
            Booking.company_id == company_id,
            Booking.scheduled_date >= start,
            Booking.scheduled_date <= end,
            Booking.status != BookingStatus.CANCELLED,
        )
        .group_by(Booking.scheduled_date)
    ).all()
    return {row[0]: row[1] for row in rows}


def _overrides(
    db: Session, company_id: uuid.UUID, start: date_type, end: date_type
) -> dict[date_type, CompanyDateCapacity]:
    rows = db.scalars(
        select(CompanyDateCapacity).where(
            CompanyDateCapacity.company_id == company_id,
            CompanyDateCapacity.date >= start,
            CompanyDateCapacity.date <= end,
        )
    )
    return {row.date: row for row in rows}


def _evaluate(
    day: date_type,
    *,
    first: date_type,
    last: date_type,
    booked: int,
    override: CompanyDateCapacity | None,
    default_capacity: int,
) -> DayAvailability:
    """Decide one day. Order matters: window rules before capacity rules."""
    if day < first:
        return DayAvailability(day, False, REASON_TOO_SOON)
    if day > last:
        return DayAvailability(day, False, REASON_TOO_FAR)
    if override is not None and override.is_blocked:
        return DayAvailability(day, False, REASON_BLOCKED)

    capacity = default_capacity
    if override is not None and override.max_moves is not None:
        capacity = override.max_moves
    if booked >= capacity:
        return DayAvailability(day, False, REASON_FULL)
    return DayAvailability(day, True)


def availability_calendar(
    db: Session,
    company: Company,
    *,
    start: date_type,
    end: date_type,
    today: date_type,
) -> list[DayAvailability]:
    """Availability for every date in ``[start, end]``, inclusive.

    :raises app.core.errors.ValidationError: the range is inverted or wider than
        :data:`MAX_CALENDAR_DAYS`.
    """
    if end < start:
        raise ValidationError("End date must not be before start date")
    span = (end - start).days + 1
    if span > MAX_CALENDAR_DAYS:
        raise ValidationError(f"Date range must be {MAX_CALENDAR_DAYS} days or fewer")

    first, last = bookable_window(company, today)
    booked = _booked_counts(db, company.id, start, end)
    overrides = _overrides(db, company.id, start, end)
    capacity = company_daily_capacity(company)

    return [
        _evaluate(
            day,
            first=first,
            last=last,
            booked=booked.get(day, 0),
            override=overrides.get(day),
            default_capacity=capacity,
        )
        for day in (start + timedelta(days=offset) for offset in range(span))
    ]


def day_availability(
    db: Session, company: Company, day: date_type, *, today: date_type
) -> DayAvailability:
    """Availability for exactly one date."""
    first, last = bookable_window(company, today)
    booked = _booked_counts(db, company.id, day, day).get(day, 0)
    override = _overrides(db, company.id, day, day).get(day)
    return _evaluate(
        day,
        first=first,
        last=last,
        booked=booked,
        override=override,
        default_capacity=company_daily_capacity(company),
    )


#: Customer-facing wording per reason. The reason codes stay stable for the UI; these
#: strings are what a person reads.
_REASON_MESSAGES = {
    REASON_BLOCKED: "That date isn't available.",
    REASON_FULL: "That date is fully booked.",
    REASON_TOO_SOON: "That date is too soon — please choose a later date.",
    REASON_TOO_FAR: "That date is too far ahead to book yet.",
}


def assert_available(db: Session, company: Company, day: date_type, *, today: date_type) -> None:
    """Raise unless ``day`` can be booked.

    Callers that persist must call this **inside their write transaction**, not only at
    preview time: a date can fill up between a customer seeing it and confirming it.

    :raises app.core.errors.ConflictError: the date cannot be booked.
    """
    result = day_availability(db, company, day, today=today)
    if not result.is_available:
        raise ConflictError(_REASON_MESSAGES.get(result.reason or "", "That date isn't available."))


# ---------------------------------------------------------------------------
# Owner calendar (authenticated dashboard)
#
# ``company_id`` always comes from the authenticated user; these never accept a tenant
# from client input, matching every other dashboard service.
# ---------------------------------------------------------------------------


def _require_company(db: Session, company_id: uuid.UUID) -> Company:
    company = db.get(Company, company_id)
    if company is None:  # pragma: no cover - FK from an authenticated user guarantees it
        raise NotFoundError("Company not found")
    return company


def owner_calendar(
    db: Session,
    company_id: uuid.UUID,
    *,
    start: date_type,
    end: date_type,
    today: date_type | None = None,
) -> list[OwnerDayOut]:
    """The same derivation as the customer sees, plus the load figures behind it."""
    company = _require_company(db, company_id)
    today = today or date_type.today()

    days = availability_calendar(db, company, start=start, end=end, today=today)
    booked = _booked_counts(db, company_id, start, end)
    overrides = _overrides(db, company_id, start, end)
    default_capacity = company_daily_capacity(company)

    result = []
    for day in days:
        override = overrides.get(day.date)
        capacity = default_capacity
        if override is not None and override.max_moves is not None:
            capacity = override.max_moves
        result.append(
            OwnerDayOut(
                date=day.date,
                is_available=day.is_available,
                reason=day.reason,
                booked_count=booked.get(day.date, 0),
                capacity=capacity,
                is_blocked=bool(override and override.is_blocked),
                note=override.note if override else None,
            )
        )
    return result


def _one_day(db: Session, company_id: uuid.UUID, day: date_type) -> OwnerDayOut:
    return owner_calendar(db, company_id, start=day, end=day)[0]


def set_date_capacity(
    db: Session, company_id: uuid.UUID, day: date_type, payload: DateCapacityIn
) -> OwnerDayOut:
    """Create or update this company's override for ``day``.

    Idempotent: setting the same values twice is a no-op rather than a duplicate row,
    which is what makes the endpoint safe to retry.
    """
    row = db.scalar(
        select(CompanyDateCapacity).where(
            CompanyDateCapacity.company_id == company_id,
            CompanyDateCapacity.date == day,
        )
    )
    if row is None:
        row = CompanyDateCapacity(company_id=company_id, date=day)
        db.add(row)

    row.max_moves = payload.max_moves
    row.is_blocked = payload.is_blocked
    row.note = (payload.note or "").strip() or None
    db.commit()
    return _one_day(db, company_id, day)


def clear_date_capacity(db: Session, company_id: uuid.UUID, day: date_type) -> None:
    """Drop the override for ``day``, restoring the company default.

    Absent is the normal state, so clearing a date that has no override succeeds
    rather than 404s — the caller's intent ("this date should be default") is satisfied
    either way.
    """
    row = db.scalar(
        select(CompanyDateCapacity).where(
            CompanyDateCapacity.company_id == company_id,
            CompanyDateCapacity.date == day,
        )
    )
    if row is not None:
        db.delete(row)
        db.commit()
