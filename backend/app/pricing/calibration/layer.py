"""The calibration layer: gate, clamp, apply, and fall back.

    MoveSpec → RuleBasedEngine → BaseEstimate → CalibrationLayer → CalibratedEstimate

The layer's job is mostly **refusing**. A model produces a number; this decides whether
there is enough evidence to use it, bounds how far it may move a price, checks the result
is physically sensible, and returns the untouched base estimate whenever any of that
fails. A calibration bug should cost us a correction, never a quote.

Money is never predicted. The layer adjusts labor hours and then rebuilds the total from
the base estimate's own parts — the company's hourly rate, its travel fee, its date
multiplier, its range spread. Every dollar still comes from ``PricingConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.core.logging import get_logger
from app.pricing.calibration.models import (
    MIN_COMPANY_MOVES,
    CalibrationFeatures,
    CalibrationModel,
)
from app.pricing.config import PricingConfig
from app.pricing.engine import Estimate, LineItem
from app.pricing.rules import round_up_half_hour
from app.pricing.spec import MoveSpec

logger = get_logger(__name__)

#: Hard ceiling on any adjustment in V1, whatever the model says and whatever the data
#: volume. A correction larger than this is not a calibration, it is a different price.
ABSOLUTE_MAX_ADJUSTMENT = 0.20

#: Evidence tiers: more history earns a wider bound, because the factor is better
#: supported. Numbers are a deliberate policy choice, not a fitted quantity.
_CLAMP_TIERS: tuple[tuple[int, float], ...] = (
    (200, 0.20),
    (50, 0.15),
    (MIN_COMPANY_MOVES, 0.10),
)

#: Regardless of the tier, calibrated hours must stay inside this band of the engine's
#: own answer. A model that wants to double a job is wrong about something.
MIN_HOURS_MULTIPLE = 0.5
MAX_HOURS_MULTIPLE = 2.0

NO_MODEL = "no_model"
INSUFFICIENT_HISTORY = "insufficient_history"
INSUFFICIENT_SUPPORT = "insufficient_support"
MODEL_ERROR = "model_error"
MISSING_FEATURES = "missing_features"
UNUSABLE_BASE = "unusable_base_estimate"


def clamp_cap_for(n_train: int) -> float | None:
    """Maximum adjustment this much history earns, or ``None`` for "not enough"."""
    for threshold, cap in _CLAMP_TIERS:
        if n_train >= threshold:
            return min(cap, ABSOLUTE_MAX_ADJUSTMENT)
    return None


@dataclass(frozen=True)
class Adjustment:
    """What the layer decided, and why — persisted verbatim for later audit."""

    factor: float
    raw_factor: float
    clamped: bool
    support: int
    segment: str | None
    cap: float | None
    reason: str | None = None

    @property
    def applied(self) -> bool:
        return self.reason is None and self.factor != 1.0


@dataclass(frozen=True)
class CalibratedEstimate:
    """The base estimate, the adjusted one, and the decision that connects them.

    Both estimates are kept so shadow mode is free: record the pair, show the base.
    """

    base: Estimate
    calibrated: Estimate
    adjustment: Adjustment

    @property
    def changed(self) -> bool:
        return self.calibrated.total_cents != self.base.total_cents


def _dec(value: float | int) -> Decimal:
    return Decimal(str(value))


def _to_cents(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)


def _to_whole_dollar_cents(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)) * 100


class CalibrationLayer:
    """Applies a company's calibration model to one base estimate."""

    def apply(
        self,
        base: Estimate,
        spec: MoveSpec,
        config: PricingConfig,
        model: CalibrationModel | None,
    ) -> CalibratedEstimate:
        """Return the base estimate plus its calibrated counterpart.

        Never raises. Every failure path returns the base estimate unchanged with a
        recorded reason, because a quote must not depend on calibration succeeding.
        """
        if model is None:
            return self._unchanged(base, NO_MODEL)

        cap = clamp_cap_for(getattr(model, "n_train", 0))
        if cap is None:
            return self._unchanged(base, INSUFFICIENT_HISTORY)
        if base.estimated_hours <= 0:
            return self._unchanged(base, UNUSABLE_BASE)

        try:
            features = CalibrationFeatures(
                home_size=spec.home_size.value,
                packing_service=spec.packing_service.value,
                base_hours=base.estimated_hours,
            )
            prediction = model.predict(features)
        except Exception:
            # A broken model costs a correction, not a quote.
            logger.exception("Calibration model failed; falling back to the rule engine")
            return self._unchanged(base, MODEL_ERROR)

        if prediction.support < MIN_COMPANY_MOVES:
            return self._unchanged(base, INSUFFICIENT_SUPPORT, support=prediction.support)

        raw = prediction.factor
        factor = min(max(raw, 1 - cap), 1 + cap)
        clamped = abs(factor - raw) > 1e-9

        adjusted_hours = self._adjusted_hours(base, config, factor)
        if not (
            MIN_HOURS_MULTIPLE * base.estimated_hours
            <= adjusted_hours
            <= MAX_HOURS_MULTIPLE * base.estimated_hours
        ):
            # Defence in depth: the clamp should already prevent this.
            return self._unchanged(base, MODEL_ERROR)

        adjustment = Adjustment(
            factor=factor,
            raw_factor=raw,
            clamped=clamped,
            support=prediction.support,
            segment=prediction.segment,
            cap=cap,
        )
        if clamped:
            logger.info(
                "Calibration clamped: raw=%.4f applied=%.4f cap=%.2f", raw, factor, cap
            )
        return CalibratedEstimate(
            base=base,
            calibrated=self._rebuild(base, config, adjusted_hours),
            adjustment=adjustment,
        )

    @staticmethod
    def _adjusted_hours(base: Estimate, config: PricingConfig, factor: float) -> float:
        """Scale hours, then apply the same billing rules the engine applies.

        Rounding and the minimum are hours-domain business rules, not engine internals —
        a calibrated quote that bills 6.37 hours would be wrong in the same way an
        uncalibrated one would.
        """
        hours = round_up_half_hour(_dec(base.estimated_hours) * _dec(factor))
        return float(max(hours, _dec(config.min_billable_hours)))

    @staticmethod
    def _rebuild(base: Estimate, config: PricingConfig, hours: float) -> Estimate:
        """Rebuild the estimate at new hours, reusing the base's own money components.

        Labor is recomputed at the company's rate; travel, the date multiplier and the
        range spread are carried over untouched. No pricing rule is re-implemented here —
        the parts are read back off the base estimate the engine produced.
        """
        labor_line = next((li for li in base.line_items if li.code == "labor"), None)
        travel_line = next((li for li in base.line_items if li.code == "travel"), None)
        if labor_line is None or travel_line is None:  # pragma: no cover - engine guarantees
            return base

        rate = _dec(float(labor_line.meta.get("hourly_rate", 0)))
        labor = _dec(hours) * rate
        travel = _dec(travel_line.amount_cents) / 100
        subtotal = labor + travel
        multiplier = _dec(base.adjustment_multiplier)
        total = subtotal * multiplier
        spread = _dec(config.range_spread_pct)

        line_items = [
            LineItem(
                code="labor",
                label=f"Moving labor — {base.crew_size} movers × {hours}h",
                amount_cents=_to_cents(labor),
                meta={
                    "crew_size": base.crew_size,
                    "hours": hours,
                    "hourly_rate": float(rate),
                    "calibrated": True,
                },
            ),
            travel_line,
        ]
        for item in base.line_items:
            if item.code == "date_adjustment":
                line_items.append(
                    LineItem(
                        code="date_adjustment",
                        label=item.label,
                        amount_cents=_to_cents(total - subtotal),
                        meta=item.meta,
                    )
                )

        return Estimate(
            engine_version=base.engine_version,
            currency=base.currency,
            crew_size=base.crew_size,
            estimated_hours=hours,
            line_items=tuple(line_items),
            subtotal_cents=_to_cents(subtotal),
            adjustment_multiplier=base.adjustment_multiplier,
            total_cents=_to_cents(total),
            amount_min_cents=_to_whole_dollar_cents(total * (1 - spread)),
            amount_max_cents=_to_whole_dollar_cents(total * (1 + spread)),
            inputs=base.inputs,
        )

    @staticmethod
    def _unchanged(base: Estimate, reason: str, *, support: int = 0) -> CalibratedEstimate:
        return CalibratedEstimate(
            base=base,
            calibrated=base,
            adjustment=Adjustment(
                factor=1.0,
                raw_factor=1.0,
                clamped=False,
                support=support,
                segment=None,
                cap=None,
                reason=reason,
            ),
        )
