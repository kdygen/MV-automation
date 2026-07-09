"""Golden and property tests for the Stage 1 rule-based pricing engine.

Golden values are hand-computed from the default config and pinned exactly — any
change to engine arithmetic must consciously update these numbers.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.moving_request import HomeSize, PackingService
from app.pricing import (
    MoveSpec,
    PricingConfig,
    PricingInputError,
    RuleBasedEngine,
)
from app.pricing.spec import SideAccess

ENGINE = RuleBasedEngine()
CONFIG = PricingConfig()

# Fixed reference dates (verified weekdays).
TUESDAY_OFF_PEAK = date(2026, 10, 13)  # Tue, mid-month, October
SATURDAY_PEAK_MONTH_END = date(2026, 5, 30)  # Sat, day>=25, May


def spec(**overrides: object) -> MoveSpec:
    defaults: dict = {
        "home_size": HomeSize.TWO_BR,
        "packing_service": PackingService.NONE,
        "special_items": (),
        "origin": SideAccess(),
        "destination": SideAccess(),
        "distance_miles": 10.0,
        "move_date": TUESDAY_OFF_PEAK,
    }
    defaults.update(overrides)
    return MoveSpec(**defaults)


class TestGoldenScenarios:
    def test_2br_partial_packing_piano_stairs(self) -> None:
        """base 5.5 + stairs 0.8 + piano 1.5 = 7.8; ×1.25 = 9.75 → 10.0h;
        3 movers @ $190 = $1900; travel $50 + $2×10 = $70; no date uplift → $1970."""
        result = ENGINE.estimate(
            spec(
                packing_service=PackingService.PARTIAL,
                special_items=("piano",),
                origin=SideAccess(floor=3, has_elevator=False, stairs_flights=2),
            ),
            CONFIG,
        )
        assert result.estimated_hours == 10.0
        assert result.crew_size == 3
        assert result.subtotal_cents == 197_000
        assert result.adjustment_multiplier == 1.0
        assert result.total_cents == 197_000
        assert result.amount_min_cents == 173_400  # 1970×0.88 = 1733.60 → $1734
        assert result.amount_max_cents == 220_600  # 1970×1.12 = 2206.40 → $2206
        assert [li.code for li in result.line_items] == ["labor", "travel"]

    def test_studio_weekend_month_end_peak(self) -> None:
        """3.0h min-billable, 2 movers @ $140 = $420; travel $56; subtotal $476;
        ×1.10×1.10×1.05 = 1.2705 → $604.758 → $604.76; range $532/$677."""
        result = ENGINE.estimate(
            spec(
                home_size=HomeSize.STUDIO,
                distance_miles=3.0,
                move_date=SATURDAY_PEAK_MONTH_END,
            ),
            CONFIG,
        )
        assert result.estimated_hours == 3.0
        assert result.crew_size == 2
        assert result.subtotal_cents == 47_600
        assert result.adjustment_multiplier == pytest.approx(1.2705)
        assert result.total_cents == 60_476
        assert result.amount_min_cents == 53_200
        assert result.amount_max_cents == 67_700
        date_line = next(li for li in result.line_items if li.code == "date_adjustment")
        assert date_line.amount_cents == 60_476 - 47_600
        assert date_line.meta["applied"] == ["weekend", "month-end", "peak season"]

    def test_4br_full_pack_elevator_both_sides(self) -> None:
        """base 9.0 + elevator 0.3×2 = 9.6; ×1.5 = 14.4 → 14.5h;
        4 movers @ $240 = $3480; travel $50+$2×25 = $100 → $3580."""
        result = ENGINE.estimate(
            spec(
                home_size=HomeSize.FOUR_BR,
                packing_service=PackingService.FULL,
                origin=SideAccess(floor=5, has_elevator=True),
                destination=SideAccess(floor=8, has_elevator=True),
                distance_miles=25.0,
            ),
            CONFIG,
        )
        assert result.estimated_hours == 14.5
        assert result.crew_size == 4
        assert result.total_cents == 358_000


class TestEngineRules:
    def test_missing_distance_raises_pricing_input_error(self) -> None:
        with pytest.raises(PricingInputError, match="distance"):
            ENGINE.estimate(spec(distance_miles=None), CONFIG)

    def test_minimum_billable_hours_floor(self) -> None:
        result = ENGINE.estimate(spec(home_size=HomeSize.STUDIO), CONFIG)
        assert result.estimated_hours == CONFIG.min_billable_hours

    def test_floor_without_flight_count_falls_back_to_floor_number(self) -> None:
        """floor=4, no elevator, flights unreported ⇒ 3 flights: 5.5 + 0.4×3 = 6.7 → 7.0h."""
        result = ENGINE.estimate(
            spec(origin=SideAccess(floor=4, has_elevator=False, stairs_flights=0)), CONFIG
        )
        assert result.estimated_hours == 7.0

    def test_unknown_special_item_uses_default_hours(self) -> None:
        base = ENGINE.estimate(spec(), CONFIG)
        with_item = ENGINE.estimate(spec(special_items=("grandfather clock",)), CONFIG)
        assert with_item.estimated_hours == base.estimated_hours + 0.5

    def test_weekend_costs_more_than_weekday(self) -> None:
        weekday = ENGINE.estimate(spec(move_date=date(2026, 10, 13)), CONFIG)
        weekend = ENGINE.estimate(spec(move_date=date(2026, 10, 17)), CONFIG)  # Saturday
        assert weekend.total_cents > weekday.total_cents

    def test_range_brackets_total(self) -> None:
        result = ENGINE.estimate(spec(), CONFIG)
        assert result.amount_min_cents <= result.total_cents <= result.amount_max_cents

    def test_deterministic(self) -> None:
        s = spec(special_items=("piano", "safe"), packing_service=PackingService.FULL)
        assert ENGINE.estimate(s, CONFIG) == ENGINE.estimate(s, CONFIG)

    def test_line_items_sum_to_total(self) -> None:
        result = ENGINE.estimate(
            spec(move_date=SATURDAY_PEAK_MONTH_END, special_items=("piano",)), CONFIG
        )
        assert sum(li.amount_cents for li in result.line_items) == result.total_cents

    def test_inputs_snapshot_roundtrips(self) -> None:
        s = spec(special_items=("piano",))
        result = ENGINE.estimate(s, CONFIG)
        assert MoveSpec.model_validate(result.inputs) == s


class TestConfigValidation:
    def test_missing_crew_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match="missing rates"):
            PricingConfig(hourly_rate_by_crew={2: 140.0})  # 3,4,5-person crews unpriced

    def test_incomplete_home_sizes_rejected(self) -> None:
        with pytest.raises(ValueError, match="every home size"):
            PricingConfig(base_hours_by_home_size={HomeSize.STUDIO: 3.0})

    def test_custom_rates_flow_through(self) -> None:
        pricier = PricingConfig(
            hourly_rate_by_crew={2: 200.0, 3: 260.0, 4: 320.0, 5: 380.0}
        )
        cheap = ENGINE.estimate(spec(), CONFIG)
        costly = ENGINE.estimate(spec(), pricier)
        assert costly.total_cents > cheap.total_cents
