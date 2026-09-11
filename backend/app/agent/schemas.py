"""Tool output schemas — the explicit allowlist of what may reach the model.

Every tool returns one of these models, constructed **field by field** from ORM rows.
Nothing is ever serialized straight from a database object, so a column added to a
model later cannot silently start leaking into agent output: it must be added here
deliberately.

Excluded everywhere, by rule: primary keys and foreign keys (``id``, ``company_id``,
``quote_id``, ``moving_request_id``, ``lead_id``, ``pricing_config_id``), the quote's
``public_token``, pricing-engine internals (``engine_version``, ``inputs_snapshot``,
line-item ``meta``), operator-only flags (``is_adjusted``, ``quote_review_mode``), and
raw payloads.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class QuoteLineItem(BaseModel):
    """One priced line, as shown on the customer's quote page.

    ``meta`` from the engine's line item is intentionally dropped: it carries internals
    such as the company's hourly rate and the applied multiplier.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    label: str
    amount_cents: int


class QuoteSummary(BaseModel):
    """What the agent may know about the bound quote.

    ``total_cents`` is deliberately absent: the customer-facing figure is the *range*,
    and exposing a fourth number invites the agent to state an amount the customer
    never saw on their quote page.
    """

    model_config = ConfigDict(frozen=True)

    status: str
    currency: str
    amount_min_cents: int
    amount_max_cents: int
    estimated_hours: float
    crew_size: int
    line_items: tuple[QuoteLineItem, ...]
    valid_until: datetime
    is_expired: bool


class SideAccessInfo(BaseModel):
    """Access details for one end of the move.

    Street address (``line1``) is excluded: the quote page reachable with this same
    token exposes only the city, so the agent's view stays no wider than the surface
    that already exists if a quote link leaks.
    """

    model_config = ConfigDict(frozen=True)

    city: str
    state: str
    zip: str
    floor: int
    has_elevator: bool
    stairs_flights: int


class MoveDetails(BaseModel):
    """What the agent may know about the bound move."""

    model_config = ConfigDict(frozen=True)

    move_date: date
    is_date_flexible: bool
    home_size: str
    packing_service: str
    special_items: tuple[str, ...]
    distance_miles: float | None
    notes: str | None
    origin: SideAccessInfo
    destination: SideAccessInfo


class CompanyInfo(BaseModel):
    """What the agent may know about the bound company.

    Built from allowlisted fields only. The ``settings`` JSON is never returned
    wholesale — it holds internal operational flags such as ``quote_review_mode``.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    phone: str | None
    email: str | None
    quote_validity_days: int


class KnowledgeEntry(BaseModel):
    """One company knowledge answer the agent may quote from.

    Only the three human-readable fields cross the boundary. ``id``, ``company_id``,
    ``is_active``, timestamps, the curated ``keywords`` (an internal retrieval aid), and
    the match score are all withheld: none of them helps answer a customer's question,
    and every one of them is something the model could otherwise repeat back.
    """

    model_config = ConfigDict(frozen=True)

    category: str
    title: str
    content: str


class KnowledgeResults(BaseModel):
    """Result of a knowledge search — possibly empty.

    An empty ``results`` tuple is a meaningful answer, not a failure: it tells the agent
    this company has published nothing on the subject, so it must say so rather than
    invent a policy.
    """

    model_config = ConfigDict(frozen=True)

    results: tuple[KnowledgeEntry, ...]


class NextStep(BaseModel):
    """Result of the navigation-hint tool: the action the frontend should offer.

    Carries the enum value and nothing else — no URL, no button label, no identifier.
    What the control says and where it leads is the frontend's decision, so the model
    cannot influence either.
    """

    model_config = ConfigDict(frozen=True)

    action: str
