"""Schemas for quote endpoints (public customer views + dashboard actions).

``QuotePublicOut`` is what a customer sees via their token link — deliberately free of
internal ids, config versions, and engine internals beyond the line items shown on the
printed quote.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class QuoteSummaryPublic(BaseModel):
    """Quote payload embedded in the intake response and quote page."""

    status: str
    public_token: str | None = None  # None while pending owner review
    currency: str = "USD"
    amount_min_cents: int | None = None
    amount_max_cents: int | None = None
    estimated_hours: float | None = None
    crew_size: int | None = None
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    valid_until: datetime | None = None


class QuotePublicOut(BaseModel):
    """Full customer-facing quote page payload."""

    company_name: str
    status: str
    currency: str
    amount_min_cents: int
    amount_max_cents: int
    estimated_hours: float
    crew_size: int
    line_items: list[dict[str, Any]]
    valid_until: datetime
    move_date: date
    origin_city: str
    destination_city: str
    home_size: str
    #: Which revision the customer is looking at; 1 unless they have edited the move.
    revision: int = 1
    #: Deposit due to book, in cents. ``0`` means this company books without payment,
    #: which is what tells the frontend whether to render "Accept & book" or
    #: "Accept & pay". The browser never sends this value back.
    deposit_cents: int = 0


class AcceptQuoteResponse(BaseModel):
    """Returned when the customer accepts: their confirmed booking."""

    booking_id: uuid.UUID
    scheduled_date: date
    crew_size: int
    status: str
    company_name: str


class ApproveQuoteIn(BaseModel):
    """Owner approval payload; optional price-range adjustment (dollars)."""

    amount_min_dollars: float | None = Field(default=None, gt=0)
    amount_max_dollars: float | None = Field(default=None, gt=0)


class QuoteAdminOut(BaseModel):
    """Dashboard view of a quote (full detail, tenant-scoped)."""

    id: uuid.UUID
    status: str
    currency: str
    amount_min_cents: int
    amount_max_cents: int
    total_cents: int
    estimated_hours: float
    crew_size: int
    engine_version: str
    is_adjusted: bool
    line_items: list[dict[str, Any]]
    public_token: str
    valid_until: datetime
    accepted_at: datetime | None
    created_at: datetime


class CheckoutOut(BaseModel):
    """Where to send the customer to pay. No amount is accepted *from* the browser."""

    checkout_url: str
    amount_cents: int
    currency: str


class PaymentStatusOut(BaseModel):
    """What the return page polls while the webhook lands.

    ``booking_confirmed`` is the only field that matters, and it is true only once a
    verified webhook has created the booking. The browser's arrival on the success URL
    does not set it.
    """

    status: str
    booking_confirmed: bool
    amount_cents: int | None = None
    currency: str | None = None
