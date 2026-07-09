"""Schemas for the authenticated company dashboard (lists, details, settings)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.pricing import PricingConfig


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    phone: str | None
    source: str
    status: str
    created_at: datetime


class RequestAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    origin_line1: str
    origin_city: str
    origin_state: str
    origin_zip: str
    destination_line1: str
    destination_city: str
    destination_state: str
    destination_zip: str
    move_date: date
    is_date_flexible: bool
    home_size: str
    packing_service: str
    special_items: list[Any]
    notes: str | None
    distance_miles: float | None
    status: str
    created_at: datetime


class QuoteListOut(BaseModel):
    """Quote row for dashboard tables, denormalized with lead + move info."""

    id: uuid.UUID
    status: str
    currency: str
    amount_min_cents: int
    amount_max_cents: int
    is_adjusted: bool
    created_at: datetime
    valid_until: datetime
    lead_name: str
    lead_email: str
    move_date: date
    home_size: str


class LeadDetailOut(BaseModel):
    lead: LeadOut
    requests: list[RequestAdminOut]
    quotes: list[QuoteListOut]


class BookingOut(BaseModel):
    """Booking row for dashboard tables, denormalized with lead + quote info."""

    id: uuid.UUID
    scheduled_date: date
    time_window: str | None
    crew_size: int
    status: str
    notes: str | None
    lead_name: str
    lead_email: str
    amount_min_cents: int
    amount_max_cents: int
    quote_id: uuid.UUID


class CompanySettingsOut(BaseModel):
    name: str
    slug: str
    email: str | None
    phone: str | None
    quote_review_mode: bool
    quote_validity_days: int


class CompanySettingsIn(BaseModel):
    """PATCH payload — only provided fields are changed."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    quote_review_mode: bool | None = None
    quote_validity_days: int | None = Field(default=None, ge=1, le=90)


class PricingSettingsOut(BaseModel):
    version: int
    config: PricingConfig


class PricingSettingsIn(BaseModel):
    """PUT payload: the full pricing config document (validated)."""

    config: PricingConfig
