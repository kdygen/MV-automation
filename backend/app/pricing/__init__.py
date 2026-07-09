"""The pricing engine — deterministic, isolated, versioned.

This package never imports FastAPI, the ORM session, or any LLM code. Its contract:

    PricingEngine.estimate(MoveSpec, PricingConfig) -> Estimate

Stage 1 (:class:`~app.pricing.rules.RuleBasedEngine`) prices local hourly moves from
per-company rules. Stages 2–4 (similar-job retrieval, ML regression, continuous
retraining) will be further implementations of the same interface, so callers never
change as the engine evolves. Every produced estimate carries the engine version and a
full input snapshot for auditability and future training data.

**Design rule: no LLM ever computes a price.**
"""

from app.pricing.config import DEFAULT_PRICING_CONFIG, PricingConfig
from app.pricing.engine import Estimate, LineItem, PricingEngine, PricingInputError
from app.pricing.rules import RULES_ENGINE_VERSION, RuleBasedEngine
from app.pricing.spec import MoveSpec

__all__ = [
    "DEFAULT_PRICING_CONFIG",
    "Estimate",
    "LineItem",
    "MoveSpec",
    "PricingConfig",
    "PricingEngine",
    "PricingInputError",
    "RULES_ENGINE_VERSION",
    "RuleBasedEngine",
]
