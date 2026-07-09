"""Stage 1 pricing: deterministic rules for local hourly moves.

The calculation, in order:

1. **Labor hours** = base hours for the home size
   + access hours for each side (elevator building: flat adder when above ground floor;
     otherwise stairs-flights × per-flight adder, with ``floor - 1`` as the fallback
     when the customer didn't count flights)
   + special-item handling hours.
2. Packing service multiplies labor hours (none/partial/full).
3. Hours round **up** to the next half hour, floored at the company minimum.
4. **Labor cost** = hours × hourly rate for the crew size implied by the home size.
5. **Travel fee** = base + per-mile × driving distance.
6. Date multipliers (weekend / month-end / peak season) scale the subtotal; the uplift
   is shown as its own line item so the customer sees why the date matters.
7. The customer-facing **range** is total × (1 ± spread), rounded to whole dollars.

All arithmetic is ``Decimal``; money leaves the engine as integer cents.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from app.pricing.config import PricingConfig
from app.pricing.engine import Estimate, LineItem, PricingInputError
from app.pricing.spec import MoveSpec, SideAccess

RULES_ENGINE_VERSION = "rules-v1.0"

_CENT = Decimal("0.01")
_DOLLAR = Decimal("1")


def _dec(value: float | int) -> Decimal:
    """Convert via str so config floats become exact decimals (0.4 -> Decimal('0.4'))."""
    return Decimal(str(value))


def _to_cents(amount: Decimal) -> int:
    return int(amount.quantize(_CENT, rounding=ROUND_HALF_UP) * 100)


def _to_whole_dollar_cents(amount: Decimal) -> int:
    return int(amount.quantize(_DOLLAR, rounding=ROUND_HALF_UP)) * 100


class RuleBasedEngine:
    """Stage 1 :class:`~app.pricing.engine.PricingEngine` implementation."""

    version = RULES_ENGINE_VERSION

    def estimate(self, spec: MoveSpec, config: PricingConfig) -> Estimate:
        if spec.distance_miles is None:
            raise PricingInputError("distance_miles is required to price a move")

        # --- 1-3: labor hours ---
        base_hours = _dec(config.base_hours_by_home_size[spec.home_size])
        access_hours = self._access_hours(spec.origin, config) + self._access_hours(
            spec.destination, config
        )
        items_hours = sum(
            (
                _dec(config.special_item_hours.get(item, config.special_item_default_hours))
                for item in spec.special_items
            ),
            Decimal(0),
        )
        raw_hours = base_hours + access_hours + items_hours
        raw_hours *= _dec(config.packing_hours_multiplier[spec.packing_service])
        hours = self._round_up_half_hour(raw_hours)
        hours = max(hours, _dec(config.min_billable_hours))

        # --- 4: labor cost ---
        crew = config.crew_by_home_size[spec.home_size]
        rate = _dec(config.hourly_rate_by_crew[crew])
        labor = hours * rate

        # --- 5: travel ---
        travel = _dec(config.travel_fee_base) + _dec(config.travel_fee_per_mile) * _dec(
            spec.distance_miles
        )

        subtotal = labor + travel

        # --- 6: date multipliers ---
        multiplier, applied = self._date_multiplier(spec, config)
        total = subtotal * multiplier

        line_items = [
            LineItem(
                code="labor",
                label=f"Moving labor — {crew} movers × {hours}h",
                amount_cents=_to_cents(labor),
                meta={"crew_size": crew, "hours": float(hours), "hourly_rate": float(rate)},
            ),
            LineItem(
                code="travel",
                label="Travel fee",
                amount_cents=_to_cents(travel),
                meta={"distance_miles": spec.distance_miles},
            ),
        ]
        if multiplier != 1:
            line_items.append(
                LineItem(
                    code="date_adjustment",
                    label="Date adjustment (" + ", ".join(applied) + ")",
                    amount_cents=_to_cents(total - subtotal),
                    meta={"multiplier": float(multiplier), "applied": applied},
                )
            )

        spread = _dec(config.range_spread_pct)
        return Estimate(
            engine_version=self.version,
            currency=config.currency,
            crew_size=crew,
            estimated_hours=float(hours),
            line_items=tuple(line_items),
            subtotal_cents=_to_cents(subtotal),
            adjustment_multiplier=float(multiplier),
            total_cents=_to_cents(total),
            amount_min_cents=_to_whole_dollar_cents(total * (1 - spread)),
            amount_max_cents=_to_whole_dollar_cents(total * (1 + spread)),
            inputs=spec.model_dump(mode="json"),
        )

    @staticmethod
    def _access_hours(side: SideAccess, config: PricingConfig) -> Decimal:
        if side.floor <= 1 and side.stairs_flights == 0:
            return Decimal(0)
        if side.has_elevator:
            return _dec(config.elevator_building_hours)
        flights = max(side.stairs_flights, side.floor - 1)
        return _dec(config.stairs_hours_per_flight) * flights

    @staticmethod
    def _round_up_half_hour(hours: Decimal) -> Decimal:
        return Decimal(math.ceil(hours * 2)) / 2

    @staticmethod
    def _date_multiplier(spec: MoveSpec, config: PricingConfig) -> tuple[Decimal, list[str]]:
        multiplier = Decimal(1)
        applied: list[str] = []
        if spec.move_date.isoweekday() >= 6:  # Saturday/Sunday
            multiplier *= _dec(config.weekend_multiplier)
            applied.append("weekend")
        if spec.move_date.day >= config.month_end_from_day:
            multiplier *= _dec(config.month_end_multiplier)
            applied.append("month-end")
        if spec.move_date.month in config.peak_season_months:
            multiplier *= _dec(config.peak_season_multiplier)
            applied.append("peak season")
        return multiplier, applied
