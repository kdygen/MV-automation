"""Schemas for customer-initiated quote changes (Step 4C).

The customer sees a side-by-side comparison before anything is written. Both sides use
the same shape so the frontend can render them with one component and no branching.

No identifiers cross the wire in either direction: the quote token in the URL is the
only scope credential, and ``extra="forbid"`` means a body carrying ``quote_id`` or
``company_id`` is rejected rather than ignored.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.moving_request import HomeSize, PackingService


class QuoteSideOut(BaseModel):
    """One side of a before/after comparison."""

    move_date: date
    currency: str
    amount_min_cents: int
    amount_max_cents: int
    estimated_hours: float
    crew_size: int
    line_items: list[dict[str, Any]] = Field(default_factory=list)


class ChangePreviewOut(BaseModel):
    """What the customer is shown before confirming. Nothing has been written yet."""

    current: QuoteSideOut
    proposed: QuoteSideOut
    price_changed: bool
    #: Signed deltas in cents; negative means the change makes the move cheaper.
    difference_min_cents: int
    difference_max_cents: int
    #: New expiry the confirmed revision would carry.
    proposed_valid_until: datetime


class ChangeDateIn(BaseModel):
    """The only thing a date change accepts."""

    model_config = ConfigDict(extra="forbid")

    move_date: date


class SideAccessIn(BaseModel):
    """Access at one end of the move. Bounds mirror ``AddressIn`` in the intake schema."""

    model_config = ConfigDict(extra="forbid")

    floor: int | None = Field(default=None, ge=1, le=100)
    has_elevator: bool | None = None
    stairs_flights: int | None = Field(default=None, ge=0, le=20)


class EditMoveIn(BaseModel):
    """Customer edits to the priced inputs. Every field is optional; unset means unchanged.

    Street addresses are deliberately **not** editable here. Changing one changes the
    driving distance, which means re-running the distance provider — so a customer edit
    would start failing whenever Maps is down, and a bad address would silently reprice
    the whole move. Address corrections stay a conversation with the company.
    """

    model_config = ConfigDict(extra="forbid")

    move_date: date | None = None
    home_size: HomeSize | None = None
    packing_service: PackingService | None = None
    special_items: list[str] | None = Field(default=None, max_length=20)
    is_date_flexible: bool | None = None
    origin: SideAccessIn | None = None
    destination: SideAccessIn | None = None

    @field_validator("special_items")
    @classmethod
    def _clean_items(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > 100 for item in cleaned):
            raise ValueError("special item names must be 100 characters or fewer")
        return cleaned


class MoveDetailsOut(BaseModel):
    """The priced inputs, as the edit form loads them."""

    move_date: date
    is_date_flexible: bool
    home_size: str
    packing_service: str
    special_items: list[str]
    origin_city: str
    origin_floor: int
    origin_has_elevator: bool
    origin_stairs_flights: int
    destination_city: str
    destination_floor: int
    destination_has_elevator: bool
    destination_stairs_flights: int
