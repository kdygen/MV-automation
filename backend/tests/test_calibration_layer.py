"""Tests for baseline recomputation, metrics, models, and the calibration layer (6A/6B).

The layer's job is mostly refusing, so most of these assert a refusal: not enough
history, not enough support for this segment, a model that misbehaves, a factor that
would move a price too far. The recurring guarantee is that **every failure path returns
the rule engine's own estimate untouched**.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.models import Job, JobSource
from app.models.moving_request import HomeSize, PackingService
from app.pricing import MoveSpec, PricingConfig, RuleBasedEngine
from app.pricing.baseline import (
    IMPLAUSIBLE_HOURS,
    IMPLAUSIBLE_RATIO,
    NO_ACCESS,
    NO_ACTUAL_HOURS,
    NO_DISTANCE,
    Excluded,
    TrainingRow,
    has_access_information,
    training_row,
)
from app.pricing.calibration import (
    ABSOLUTE_MAX_ADJUSTMENT,
    MIN_COMPANY_MOVES,
    MIN_SEGMENT_SUPPORT,
    CalibrationFeatures,
    CalibrationLayer,
    GlobalScalarModel,
    SegmentedModel,
    build_model,
    clamp_cap_for,
    load_model,
)
from app.pricing.calibration.layer import (
    INSUFFICIENT_HISTORY,
    MODEL_ERROR,
    NO_MODEL,
)
from app.pricing.metrics import bootstrap_improvement, hours_metrics
from app.pricing.spec import SideAccess

CONFIG = PricingConfig()
ENGINE = RuleBasedEngine()
LAYER = CalibrationLayer()
PAST = date(2026, 3, 10)  # a Tuesday, mid-month: no date multipliers


def spec(**over) -> MoveSpec:
    values = {
        "home_size": HomeSize.TWO_BR,
        "packing_service": PackingService.NONE,
        "distance_miles": 18.0,
        "move_date": PAST,
        "origin": SideAccess(floor=2, has_elevator=False, stairs_flights=1),
    }
    values.update(over)
    return MoveSpec(**values)


def base_estimate(**over):
    return ENGINE.estimate(spec(**over), CONFIG)


def job(**over) -> Job:
    values = {
        "company_id": None,
        "source": JobSource.IMPORT,
        "move_date": PAST,
        "home_size": HomeSize.TWO_BR,
        "packing_service": PackingService.NONE,
        "distance_miles": 18.0,
        "origin_floor": 2,
        "origin_stairs_flights": 1,
        "origin_has_elevator": False,
        "actual_hours": 7.0,
        "actual_crew_size": 3,
    }
    values.update(over)
    return Job(**values)


# --------------------------------------------------------------------------- baseline


class TestBaselineRecomputation:
    def test_builds_a_training_row_from_a_historical_move(self) -> None:
        row = training_row(job(), CONFIG, distance_miles=None)
        assert isinstance(row, TrainingRow)
        assert row.base_hours > 0
        assert row.actual_hours == 7.0
        assert row.log_ratio == pytest.approx(math.log(7.0 / row.base_hours))

    def test_the_baseline_is_our_engine_not_their_quoted_hours(self) -> None:
        """A legacy export's estimate is another system's bias, not ours."""
        row = training_row(job(quoted_hours=99.0), CONFIG, distance_miles=None)
        assert isinstance(row, TrainingRow)
        assert row.base_hours != 99.0
        assert row.base_hours == base_estimate().estimated_hours

    def test_a_move_with_no_outcome_is_excluded(self) -> None:
        result = training_row(job(actual_hours=None), CONFIG, distance_miles=None)
        assert isinstance(result, Excluded) and result.reason == NO_ACTUAL_HOURS

    @pytest.mark.parametrize("hours", [0.1, 60.0])
    def test_impossible_durations_are_excluded(self, hours: float) -> None:
        result = training_row(job(actual_hours=hours), CONFIG, distance_miles=None)
        assert isinstance(result, Excluded) and result.reason == IMPLAUSIBLE_HOURS

    def test_a_wild_ratio_is_excluded_as_a_data_error(self) -> None:
        """Four times the estimate is a mis-keyed row, not evidence about the engine."""
        result = training_row(job(actual_hours=40.0), CONFIG, distance_miles=None)
        assert isinstance(result, Excluded) and result.reason in (
            IMPLAUSIBLE_RATIO,
            IMPLAUSIBLE_HOURS,
        )

    def test_no_distance_and_no_zips_is_excluded_not_guessed(self) -> None:
        result = training_row(job(distance_miles=None), CONFIG, distance_miles=None)
        assert isinstance(result, Excluded) and result.reason == NO_DISTANCE

    def test_a_derived_distance_makes_a_row_usable_and_is_flagged(self) -> None:
        row = training_row(job(distance_miles=None), CONFIG, distance_miles=21.0)
        assert isinstance(row, TrainingRow)
        assert row.distance_derived is True
        assert row.distance_miles == 21.0

    def test_missing_access_is_excluded_to_prevent_double_counting(self) -> None:
        """Unknown access is priced as ground-floor, so the factor would absorb stairs.

        Applying that factor to a new quote that *does* record stairs — as our intake
        form always does — would count the same stairs twice.
        """
        result = training_row(
            job(origin_floor=None, origin_stairs_flights=None), CONFIG, distance_miles=None
        )
        assert isinstance(result, Excluded) and result.reason == NO_ACCESS

    def test_access_on_the_origin_alone_is_enough(self) -> None:
        assert has_access_information(job(origin_floor=3, origin_stairs_flights=None)) is True
        assert has_access_information(job(origin_floor=None, origin_stairs_flights=2)) is True
        assert has_access_information(job(origin_floor=None, origin_stairs_flights=None)) is False


# --------------------------------------------------------------------------- metrics


class TestMetrics:
    def test_reports_error_and_direction_separately(self) -> None:
        m = hours_metrics([6.0] * 10, [7.0, 7.0, 7.0, 7.0, 7.0, 5.0, 5.0, 5.0, 5.0, 5.0])
        assert m.median_ape_pct == pytest.approx(16.67, abs=0.1)
        # Equal over- and under-runs: large error, no bias a scalar could fix.
        assert abs(m.median_signed_error_pct) < 17

    def test_a_one_sided_miss_shows_as_bias(self) -> None:
        m = hours_metrics([6.0] * 10, [7.2] * 10)
        assert m.median_signed_error_pct == pytest.approx(20.0, abs=0.1)

    def test_small_samples_are_not_reportable(self) -> None:
        assert hours_metrics([6.0] * 3, [7.0] * 3).reportable is False
        assert hours_metrics([6.0] * 10, [7.0] * 10).reportable is True

    def test_improvement_carries_an_interval(self) -> None:
        actual = [7.0 + (i % 3) * 0.1 for i in range(40)]
        imp = bootstrap_improvement([6.0] * 40, [7.0] * 40, actual)
        assert imp is not None and imp.significant is True
        assert imp.ci_low_pct <= imp.relative_improvement_pct <= imp.ci_high_pct

    def test_no_real_improvement_is_not_significant(self) -> None:
        actual = [7.0, 5.0] * 20
        imp = bootstrap_improvement([6.0] * 40, [6.05] * 40, actual)
        assert imp is not None and imp.significant is False

    def test_improvement_is_reproducible(self) -> None:
        """A promotion decision must not change when the evaluation is re-run."""
        actual = [7.0 + (i % 5) * 0.2 for i in range(30)]
        a = bootstrap_improvement([6.0] * 30, [7.0] * 30, actual)
        b = bootstrap_improvement([6.0] * 30, [7.0] * 30, actual)
        assert a == b


# --------------------------------------------------------------------------- models


class TestModels:
    def test_global_scalar_learns_the_median_bias(self) -> None:
        model = GlobalScalarModel.fit([math.log(1.2)] * 25)
        prediction = model.predict(CalibrationFeatures("2br", "none", 6.0))
        assert math.exp(prediction.log_ratio) == pytest.approx(1.2)
        assert prediction.support == 25

    def test_the_median_resists_a_single_bad_row(self) -> None:
        clean = GlobalScalarModel.fit([math.log(1.1)] * 20)
        poisoned = GlobalScalarModel.fit([math.log(1.1)] * 20 + [math.log(3.5)])
        assert poisoned.log_ratio == pytest.approx(clean.log_ratio, abs=0.01)

    def test_segments_shrink_toward_the_company_factor(self) -> None:
        rows = [("2br", math.log(1.25))] * 30 + [("4br", math.log(1.6))] * 10
        model = SegmentedModel.fit(rows)
        four_br = model.segments["4br"]
        assert four_br["raw_log_ratio"] > four_br["log_ratio"] > model.global_log_ratio

    def test_a_thin_segment_falls_back_to_the_company_factor(self) -> None:
        rows = [("2br", math.log(1.2))] * 30 + [("5br_plus", math.log(2.0))] * 3
        model = SegmentedModel.fit(rows)
        prediction = model.predict(CalibrationFeatures("5br_plus", "none", 11.0))
        assert prediction.segment == "all"
        assert prediction.log_ratio == pytest.approx(model.global_log_ratio)

    def test_an_unseen_segment_falls_back(self) -> None:
        model = SegmentedModel.fit([("2br", math.log(1.2))] * 30)
        assert model.predict(CalibrationFeatures("studio", "none", 3.0)).segment == "all"

    def test_models_round_trip_through_stored_params(self) -> None:
        """Reproducing an old quote depends on this exactly."""
        original = build_model("segmented_home_size", [("2br", 0.18)] * 30)
        restored = load_model("segmented_home_size", original.to_params())
        features = CalibrationFeatures("2br", "none", 6.0)
        assert restored.predict(features) == original.predict(features)

    def test_unknown_algorithms_are_refused(self) -> None:
        with pytest.raises(ValueError):
            load_model("deep_neural_pricing", {})
        with pytest.raises(ValueError):
            build_model("deep_neural_pricing", [])

    def test_models_see_no_price_or_crew_feature(self) -> None:
        fields = set(CalibrationFeatures.__dataclass_fields__)
        assert fields == {"home_size", "packing_service", "base_hours"}
        assert not any("price" in f or "cents" in f or "crew" in f for f in fields)


# --------------------------------------------------------------------------- gating


class TestClampPolicy:
    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            (0, None),
            (19, None),
            (20, 0.10),
            (49, 0.10),
            (50, 0.15),
            (199, 0.15),
            (200, 0.20),
            (10_000, 0.20),
        ],
    )
    def test_tiers_match_the_agreed_policy(self, n: int, expected: float | None) -> None:
        assert clamp_cap_for(n) == expected

    def test_nothing_can_exceed_the_absolute_ceiling(self) -> None:
        assert max(c for c in (clamp_cap_for(n) for n in (20, 50, 200, 10**6)) if c) <= (
            ABSOLUTE_MAX_ADJUSTMENT
        )


class TestLayer:
    def test_no_model_returns_the_base_estimate_untouched(self) -> None:
        base = base_estimate()
        result = LAYER.apply(base, spec(), CONFIG, None)
        assert result.calibrated == base
        assert result.adjustment.reason == NO_MODEL
        assert result.changed is False

    def test_too_little_history_returns_the_base_estimate(self) -> None:
        model = GlobalScalarModel.fit([math.log(1.2)] * (MIN_COMPANY_MOVES - 1))
        result = LAYER.apply(base_estimate(), spec(), CONFIG, model)
        assert result.adjustment.reason == INSUFFICIENT_HISTORY
        assert result.changed is False

    def test_a_supported_factor_is_applied_to_hours(self) -> None:
        base = base_estimate()
        model = GlobalScalarModel.fit([math.log(1.1)] * 60)
        result = LAYER.apply(base, spec(), CONFIG, model)
        assert result.adjustment.applied is True
        assert result.calibrated.estimated_hours > base.estimated_hours
        assert result.calibrated.total_cents > base.total_cents

    def test_an_extreme_model_is_clamped_to_the_tier(self) -> None:
        base = base_estimate()
        model = GlobalScalarModel.fit([math.log(3.0)] * 60)  # wants +200%
        result = LAYER.apply(base, spec(), CONFIG, model)
        assert result.adjustment.clamped is True
        assert result.adjustment.factor == pytest.approx(1.15)
        assert result.adjustment.raw_factor == pytest.approx(3.0)

    def test_clamping_is_tighter_with_less_history(self) -> None:
        base = base_estimate()
        thin = LAYER.apply(base, spec(), CONFIG, GlobalScalarModel.fit([math.log(3.0)] * 25))
        thick = LAYER.apply(base, spec(), CONFIG, GlobalScalarModel.fit([math.log(3.0)] * 400))
        assert thin.adjustment.factor == pytest.approx(1.10)
        assert thick.adjustment.factor == pytest.approx(1.20)

    def test_a_model_that_raises_falls_back_and_does_not_propagate(self) -> None:
        class Broken:
            algorithm = "broken"
            n_train = 500

            def predict(self, features):  # noqa: ANN001, ANN201
                raise RuntimeError("model exploded")

            def to_params(self):  # noqa: ANN201
                return {}

        base = base_estimate()
        result = LAYER.apply(base, spec(), CONFIG, Broken())
        assert result.calibrated == base
        assert result.adjustment.reason == MODEL_ERROR

    def test_calibrated_hours_respect_the_billing_minimum(self) -> None:
        base = base_estimate(home_size=HomeSize.STUDIO)
        model = GlobalScalarModel.fit([math.log(0.5)] * 400)
        result = LAYER.apply(base, spec(home_size=HomeSize.STUDIO), CONFIG, model)
        assert result.calibrated.estimated_hours >= CONFIG.min_billable_hours

    def test_calibrated_hours_stay_on_the_half_hour(self) -> None:
        model = GlobalScalarModel.fit([math.log(1.07)] * 400)
        result = LAYER.apply(base_estimate(), spec(), CONFIG, model)
        assert (result.calibrated.estimated_hours * 2) % 1 == 0

    def test_money_is_rebuilt_from_the_companys_own_rate(self) -> None:
        """Calibration never predicts price; it moves hours and the config does the rest."""
        base = base_estimate()
        model = GlobalScalarModel.fit([math.log(1.2)] * 400)
        result = LAYER.apply(base, spec(), CONFIG, model)

        labor = next(li for li in result.calibrated.line_items if li.code == "labor")
        base_labor = next(li for li in base.line_items if li.code == "labor")
        assert labor.meta["hourly_rate"] == base_labor.meta["hourly_rate"]
        assert labor.meta["crew_size"] == base_labor.meta["crew_size"]
        expected = round(labor.meta["hourly_rate"] * result.calibrated.estimated_hours * 100)
        assert labor.amount_cents == pytest.approx(expected, abs=1)

    def test_travel_and_crew_are_not_touched(self) -> None:
        base = base_estimate()
        model = GlobalScalarModel.fit([math.log(1.2)] * 400)
        result = LAYER.apply(base, spec(), CONFIG, model)
        travel_before = next(li for li in base.line_items if li.code == "travel")
        travel_after = next(li for li in result.calibrated.line_items if li.code == "travel")
        assert travel_after.amount_cents == travel_before.amount_cents
        assert result.calibrated.crew_size == base.crew_size

    def test_the_date_multiplier_is_preserved(self) -> None:
        saturday = date(2026, 3, 14)
        base = base_estimate(move_date=saturday)
        assert base.adjustment_multiplier > 1
        model = GlobalScalarModel.fit([math.log(1.1)] * 400)
        result = LAYER.apply(base, spec(move_date=saturday), CONFIG, model)
        assert result.calibrated.adjustment_multiplier == base.adjustment_multiplier

    def test_the_range_keeps_the_configured_spread(self) -> None:
        model = GlobalScalarModel.fit([math.log(1.15)] * 400)
        result = LAYER.apply(base_estimate(), spec(), CONFIG, model)
        cal = result.calibrated
        assert cal.amount_min_cents < cal.total_cents < cal.amount_max_cents

    def test_a_neutral_model_leaves_the_price_alone(self) -> None:
        base = base_estimate()
        result = LAYER.apply(base, spec(), CONFIG, GlobalScalarModel.fit([0.0] * 400))
        assert result.calibrated.estimated_hours == base.estimated_hours
        assert result.calibrated.total_cents == base.total_cents

    def test_the_base_estimate_is_always_returned_alongside(self) -> None:
        """Shadow mode depends on having both numbers from a single call."""
        base = base_estimate()
        result = LAYER.apply(base, spec(), CONFIG, GlobalScalarModel.fit([math.log(1.2)] * 400))
        assert result.base == base
        assert result.calibrated != base

    def test_segment_support_below_the_floor_uses_the_company_factor(self) -> None:
        rows = [("2br", math.log(1.05))] * 40 + [("4br", math.log(1.9))] * (MIN_SEGMENT_SUPPORT - 1)
        model = SegmentedModel.fit(rows)
        result = LAYER.apply(
            base_estimate(home_size=HomeSize.FOUR_BR),
            spec(home_size=HomeSize.FOUR_BR),
            CONFIG,
            model,
        )
        assert result.adjustment.segment == "all"


class TestEngineUntouched:
    def test_the_rule_engine_produces_the_same_number_as_before_step_6(self) -> None:
        """Golden check: Step 6 must not have moved the baseline it is calibrating."""
        estimate = ENGINE.estimate(
            MoveSpec(
                home_size=HomeSize.TWO_BR,
                packing_service=PackingService.NONE,
                distance_miles=18.0,
                move_date=date(2026, 3, 10),
                origin=SideAccess(floor=2, has_elevator=False, stairs_flights=1),
            ),
            PricingConfig(),
        )
        assert estimate.estimated_hours == 6.0
        assert estimate.crew_size == 3
        assert estimate.total_cents == 122_600
        assert estimate.engine_version == "rules-v1.0"

    def test_half_hour_rounding_is_shared_not_duplicated(self) -> None:
        from decimal import Decimal

        from app.pricing.rules import round_up_half_hour

        assert round_up_half_hour(Decimal("6.01")) == Decimal("6.5")
        assert round_up_half_hour(Decimal("6.5")) == Decimal("6.5")
        assert RuleBasedEngine._round_up_half_hour(Decimal("6.01")) == round_up_half_hour(
            Decimal("6.01")
        )
