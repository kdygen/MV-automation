"""Schemas for the public intake endpoint (moving request submission).

The response envelope already carries a ``quote`` slot: it is ``None`` until the
pricing milestone lands, at which point the same endpoint starts returning an instant
quote without a breaking change to the contract.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.moving_request import HomeSize, PackingService


class ContactIn(BaseModel):
    """Customer contact details — becomes a lead."""

    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)


class AddressIn(BaseModel):
    """One end of the move, including access difficulty (US format)."""

    line1: str = Field(min_length=1, max_length=300)
    city: str = Field(min_length=1, max_length=120)
    state: str = Field(min_length=2, max_length=2, description="Two-letter US state code")
    zip: str = Field(pattern=r"^\d{5}(-\d{4})?$", description="US ZIP code")
    floor: int = Field(default=1, ge=1, le=100)
    has_elevator: bool = False
    stairs_flights: int = Field(default=0, ge=0, le=20)

    @field_validator("state")
    @classmethod
    def _uppercase_state(cls, value: str) -> str:
        return value.upper()


class MovingRequestIn(BaseModel):
    """Full public form submission."""

    contact: ContactIn
    origin: AddressIn
    destination: AddressIn
    move_date: date
    is_date_flexible: bool = False
    home_size: HomeSize
    packing_service: PackingService = PackingService.NONE
    special_items: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("move_date")
    @classmethod
    def _not_in_past(cls, value: date) -> date:
        if value < date.today():
            raise ValueError("move_date cannot be in the past")
        return value


class MovingRequestOut(BaseModel):
    """Echo of the persisted, structured request (the customer-confirmation view)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    move_date: date
    is_date_flexible: bool
    home_size: HomeSize
    packing_service: PackingService
    special_items: list[Any]
    distance_miles: float | None
    status: str


class IntakeResponse(BaseModel):
    """Response envelope for a submitted moving request."""

    lead_id: uuid.UUID
    request: MovingRequestOut
    # Populated once the pricing milestone lands; None means "no instant quote yet".
    quote: dict[str, Any] | None = None
