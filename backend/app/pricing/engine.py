"""Engine contract: the interface every pricing stage implements.

Money is integer **cents** end-to-end in :class:`Estimate` — floats never touch a
price. ``Estimate`` is a pydantic model so it can be snapshotted into a quote row as
JSON verbatim and later replayed for audits or training data.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.pricing.config import PricingConfig
from app.pricing.spec import MoveSpec


class PricingInputError(Exception):
    """The spec lacks data required to price (e.g. unknown distance).

    Callers decide policy: the quote service treats this as "cannot instant-quote —
    park the request for manual review", never as a hard failure that loses the lead.
    """


class LineItem(BaseModel):
    """One display line of an estimate; amounts in cents."""

    model_config = ConfigDict(frozen=True)

    code: str  # stable machine key: "labor", "travel", "date_adjustment", ...
    label: str  # human text shown on the quote
    amount_cents: int
    meta: dict[str, Any] = {}


class Estimate(BaseModel):
    """The engine's full output — auditable and JSON-serializable."""

    model_config = ConfigDict(frozen=True)

    engine_version: str
    currency: str
    crew_size: int
    estimated_hours: float
    line_items: tuple[LineItem, ...]
    subtotal_cents: int
    adjustment_multiplier: float  # product of applied date multipliers (1.0 = none)
    total_cents: int
    # Customer-facing non-binding range (rounded to whole dollars, in cents).
    amount_min_cents: int
    amount_max_cents: int
    inputs: dict[str, Any]  # MoveSpec snapshot the estimate was computed from


class PricingEngine(Protocol):
    """Every pricing stage (rules, retrieval, ML) implements exactly this."""

    version: str

    def estimate(self, spec: MoveSpec, config: PricingConfig) -> Estimate:
        """Price a move. Deterministic: same spec + config ⇒ same estimate.

        :raises PricingInputError: when the spec is missing required data.
        """
        ...
