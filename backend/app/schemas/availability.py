"""Schemas for availability: the customer's date picker and the owner's calendar.

Two audiences, two shapes, on purpose. The public payload says only whether a date can
be booked and, coarsely, why not — never how many jobs the company already has that
day, which is competitive information a quote token should not unlock.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class DayAvailabilityOut(BaseModel):
    """One day in the customer's picker."""

    date: date
    is_available: bool
    #: Coarse code (``fully_booked``, ``too_soon``, …) for UI copy; ``None`` when available.
    reason: str | None = None


class AvailabilityOut(BaseModel):
    """A window of dates plus the context the picker needs to render itself."""

    days: list[DayAvailabilityOut]
    #: The move date currently on the quote, so the picker can mark it.
    current_move_date: date
    first_bookable_date: date
    last_bookable_date: date


class OwnerDayOut(BaseModel):
    """One day in the owner's calendar — includes the load the customer never sees."""

    date: date
    is_available: bool
    reason: str | None = None
    booked_count: int
    capacity: int
    is_blocked: bool
    note: str | None = None


class DateCapacityIn(BaseModel):
    """Owner upsert for one date. ``max_moves=None`` restores the company default."""

    model_config = ConfigDict(extra="forbid")

    max_moves: int | None = Field(default=None, ge=0, le=50)
    is_blocked: bool = False
    note: str | None = Field(default=None, max_length=200)
