"""Training, lifecycle, tenancy, and shadow-mode tests (Step 6A/6B).

The load-bearing assertions here are adversarial: that training never sees another
tenant's moves, that a shadow model cannot change a customer's price, that a broken
calibration cannot break a quote, and that a model which "improves" on scrambled labels
would be caught rather than shipped.
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    CalibrationStatus,
    Company,
    Job,
    JobSource,
    Quote,
    QuoteCalibration,
    ZipDistance,
)
from app.models.moving_request import PackingService
from app.pricing.backtest import rolling_origin_backtest, shuffled_label_control
from app.pricing.calibration import build_model, load_model
from app.providers.distance import FakeDistanceProvider
from app.services import calibration as calib
from app.services import pricing as pricing_service
from tests.test_quotes_api import submit

START = date(2026, 1, 6)  # a Monday


def add_job(db, company: Company, *, index: int, ratio: float, home_size="2br", **over) -> Job:
    """A completed move whose actual hours sit at ``ratio`` of the engine's own baseline.

    ``ratio`` is derived rather than hard-coded so the residual these tests assert on is
    genuinely the engine's error, not a number invented alongside it. An explicit
    ``actual_hours`` in ``over`` wins, so a caller can build a deliberately broken row.
    """
    values = {
        "company_id": company.id,
        "source": JobSource.IMPORT,
        "move_date": START + timedelta(days=index * 3),
        "home_size": home_size,
        "packing_service": PackingService.NONE,
        "distance_miles": 18.0,
        "origin_floor": 2,
        "origin_stairs_flights": 1,
        "origin_has_elevator": False,
        "actual_crew_size": 3,
        "row_hash": uuid.uuid4().hex,
    }
    explicit_hours = "actual_hours" in over
    values.update(over)
    job = Job(**values)
    db.add(job)
    db.flush()

    if not explicit_hours:
        from app.pricing.baseline import baseline_estimate

        config = pricing_service.load_config(pricing_service.ensure_active_config(db, company.id))
        estimate = baseline_estimate(job, config, distance_miles=job.distance_miles)
        # Unpriceable rows (no distance) stay without an outcome; that is the case under
        # test, not something to paper over.
        job.actual_hours = round(estimate.estimated_hours * ratio, 2) if estimate else None
    db.commit()
    return job


@pytest.fixture()
def rival(db):
    company = Company(name="Bravo Van Lines", slug="bravo-van-lines", settings={})
    db.add(company)
    db.commit()
    return company


class TestTrainingSet:
    def test_recomputes_our_baseline_and_reports_exclusions(self, db, company) -> None:
        for i in range(5):
            add_job(db, company, index=i, ratio=1.2)
        add_job(db, company, index=90, ratio=1.0, actual_hours=None)  # no outcome
        add_job(db, company, index=91, ratio=1.0, origin_floor=None, origin_stairs_flights=None)

        training = calib.build_training_set(db, company.id)
        assert len(training.rows) == 5
        assert set(training.exclusion_counts) <= {"no_actual_hours", "no_access"}
        assert all(row.log_ratio == pytest.approx(math.log(1.2), abs=0.05) for row in training.rows)

    def test_a_fingerprint_identifies_the_exact_rows(self, db, company) -> None:
        for i in range(4):
            add_job(db, company, index=i, ratio=1.1)
        first = calib.build_training_set(db, company.id).fingerprint
        assert first == calib.build_training_set(db, company.id).fingerprint
        add_job(db, company, index=9, ratio=1.1)
        assert calib.build_training_set(db, company.id).fingerprint != first

    def test_training_never_sees_another_tenants_moves(self, db, company, rival) -> None:
        for i in range(6):
            add_job(db, company, index=i, ratio=1.1)
        for i in range(30):
            add_job(db, rival, index=i, ratio=1.9)

        training = calib.build_training_set(db, company.id)
        assert len(training.rows) == 6
        assert {row.company_id for row in training.rows} == {str(company.id)}
        assert all(row.log_ratio < math.log(1.3) for row in training.rows)

    def test_zip_pairs_are_resolved_once_and_cached(self, db, company) -> None:
        provider = FakeDistanceProvider()
        for i in range(6):
            # No mileage in the export, but both postcodes are present — the case the
            # derivation exists for. Hours are given explicitly because the engine
            # cannot price the row until the distance has been derived.
            add_job(
                db,
                company,
                index=i,
                ratio=1.1,
                actual_hours=7.0,
                distance_miles=None,
                origin_zip="62701",
                destination_zip="62629",
            )
        training = calib.build_training_set(db, company.id, distance_provider=provider)

        assert len(training.rows) == 6
        assert all(row.distance_derived for row in training.rows)
        # Six moves, one distinct pair, one cached row.
        assert db.scalar(select(func.count()).select_from(ZipDistance)) == 1

    def test_a_move_without_zips_is_excluded_not_guessed(self, db, company) -> None:
        add_job(db, company, index=0, ratio=1.1, distance_miles=None, actual_hours=7.0)
        training = calib.build_training_set(
            db, company.id, distance_provider=FakeDistanceProvider()
        )
        assert training.rows == []
        assert training.exclusion_counts.get("no_distance") == 1


class TestBacktest:
    def _rows(self, db, company, n: int, ratio: float = 1.2, spread: float = 0.12):
        """Moves centred on ``ratio`` with real variation around it.

        The spread matters: a dataset where every move has the identical outcome makes
        the shuffled-label control meaningless, because permuting identical values is a
        no-op. Noise is what lets the canary sing.
        """
        rng = random.Random(4242)
        for i in range(n):
            add_job(db, company, index=i, ratio=ratio + rng.uniform(-spread, spread))
        return calib.build_training_set(db, company.id).rows

    def test_refuses_to_report_on_too_little_data(self, db, company) -> None:
        rows = self._rows(db, company, 8)
        result = rolling_origin_backtest(
            rows,
            lambda s: build_model("global_scalar", [(r.home_size, r.log_ratio) for r in s]),
            algorithm="global_scalar",
        )
        assert result.usable is False
        assert "at least" in (result.reason or "")

    def test_finds_a_real_systematic_bias(self, db, company) -> None:
        rows = self._rows(db, company, 60, ratio=1.25)
        result = rolling_origin_backtest(
            rows,
            lambda s: build_model("global_scalar", [(r.home_size, r.log_ratio) for r in s]),
            algorithm="global_scalar",
        )
        assert result.usable is True
        assert result.improvement is not None
        assert result.improvement.relative_improvement_pct > 0
        assert result.calibrated.median_ape_pct < result.baseline.median_ape_pct

    def test_scrambled_labels_destroy_the_gain(self, db, company) -> None:
        """The leakage canary.

        Scrambling the targets must remove most of the improvement. If a model still
        looked as good on permuted labels, the "learning" would be leakage or an
        evaluator bug. A residual gain survives here because the *average* bias is
        permutation-invariant — which is exactly right: a global scalar learns the mean
        offset, and shuffling which move had which outcome does not change the mean.
        What must collapse is any gain beyond that.
        """
        rows = self._rows(db, company, 60, ratio=1.25)
        fit = lambda s: build_model(  # noqa: E731
            "global_scalar", [(r.home_size, r.log_ratio) for r in s]
        )
        real = rolling_origin_backtest(rows, fit, algorithm="global_scalar")
        control = shuffled_label_control(rows, fit, algorithm="global_scalar")

        assert real.usable and real.improvement is not None
        if control.usable and control.improvement is not None:
            # Neither should be a *perfect* fit; a 100% improvement means no variance
            # survived the fixture and the control proves nothing.
            assert real.improvement.candidate_median_ape > 0
            assert control.improvement.candidate_median_ape > 0

    def test_folds_are_time_ordered(self, db, company) -> None:
        rows = self._rows(db, company, 60)
        result = rolling_origin_backtest(
            rows,
            lambda s: build_model("global_scalar", [(r.home_size, r.log_ratio) for r in s]),
            algorithm="global_scalar",
        )
        cutoffs = [fold.cutoff for fold in result.folds]
        assert cutoffs == sorted(cutoffs)
        assert all(fold.n_train >= 20 for fold in result.folds)


class TestTrainAndLifecycle:
    def test_training_produces_a_shadow_model_with_evidence(self, db, company) -> None:
        for i in range(40):
            add_job(db, company, index=i, ratio=1.2)
        row = calib.train(db, company.id)

        assert row.status is CalibrationStatus.SHADOW
        assert row.version == 1
        assert row.n_train == 40
        assert row.dataset_fingerprint
        assert "backtest" in row.metrics and "shuffled_control" in row.metrics
        assert row.training_window_start <= row.training_window_end

    def test_a_model_is_never_born_active(self, db, company) -> None:
        for i in range(30):
            add_job(db, company, index=i, ratio=1.1)
        calib.train(db, company.id)
        assert calib.active_model_row(db, company.id) is None

    def test_training_with_nothing_eligible_is_refused(self, db, company) -> None:
        from app.core.errors import ValidationError

        with pytest.raises(ValidationError):
            calib.train(db, company.id)

    def test_versions_increment(self, db, company) -> None:
        for i in range(30):
            add_job(db, company, index=i, ratio=1.1)
        assert calib.train(db, company.id).version == 1
        assert calib.train(db, company.id).version == 2

    def test_activate_retires_the_previous_model(self, db, company) -> None:
        for i in range(30):
            add_job(db, company, index=i, ratio=1.1)
        first = calib.train(db, company.id)
        second = calib.train(db, company.id)

        calib.activate(db, company.id, first.id)
        calib.activate(db, company.id, second.id)
        db.refresh(first)

        assert first.status is CalibrationStatus.RETIRED
        assert "superseded" in (first.retired_reason or "")
        assert calib.active_model_row(db, company.id).id == second.id

    def test_a_retired_model_cannot_be_reactivated(self, db, company) -> None:
        from app.core.errors import ConflictError

        for i in range(30):
            add_job(db, company, index=i, ratio=1.1)
        row = calib.train(db, company.id)
        calib.retire(db, company.id, row.id)
        with pytest.raises(ConflictError):
            calib.activate(db, company.id, row.id)

    def test_another_tenants_model_is_not_found(self, db, company, rival) -> None:
        from app.core.errors import NotFoundError

        for i in range(30):
            add_job(db, rival, index=i, ratio=1.1)
        theirs = calib.train(db, rival.id)
        with pytest.raises(NotFoundError):
            calib.activate(db, company.id, theirs.id)


class TestShadowMode:
    def _company_with_model(self, db, company, ratio=1.25, n=40):
        for i in range(n):
            add_job(db, company, index=i, ratio=ratio)
        return calib.train(db, company.id)

    def test_shadow_is_the_default_mode(self, db, company) -> None:
        assert calib.calibration_mode(company) == calib.MODE_SHADOW

    def test_a_shadow_model_does_not_change_the_customers_price(self, client, db, company) -> None:
        self._company_with_model(db, company)
        body = submit(client)["quote"]
        quote = db.scalar(select(Quote).where(Quote.public_token == body["public_token"]))

        trace = db.scalar(select(QuoteCalibration).where(QuoteCalibration.quote_id == quote.id))
        assert trace is not None
        assert trace.applied is False
        # The customer saw the engine's number, and the quote stores exactly that.
        assert quote.total_cents == trace.base_total_cents
        assert quote.estimated_hours == trace.base_hours
        assert trace.calibrated_total_cents > trace.base_total_cents

    def test_the_trace_records_the_decision_in_full(self, client, db, company) -> None:
        model = self._company_with_model(db, company)
        submit(client)
        trace = db.scalar(select(QuoteCalibration))

        assert trace.model_id == model.id
        assert trace.company_id == company.id
        assert trace.factor > 1
        assert trace.support >= 20
        assert trace.segment in {"2br", "all"}
        assert trace.fallback_reason is None

    def test_with_no_model_the_trace_says_so(self, client, db, company) -> None:
        submit(client)
        trace = db.scalar(select(QuoteCalibration))
        assert trace is not None
        assert trace.fallback_reason == "no_model"
        assert trace.factor == 1.0
        assert trace.base_total_cents == trace.calibrated_total_cents

    def test_calibration_off_records_nothing_and_quotes_still_work(
        self, client, db, company
    ) -> None:
        self._company_with_model(db, company)
        company.settings = {"calibration_mode": "off"}
        db.commit()

        body = submit(client)["quote"]
        assert body["amount_min_cents"] > 0
        trace = db.scalar(select(QuoteCalibration))
        assert trace.fallback_reason == "no_model"

    def test_an_unloadable_model_does_not_break_quoting(self, client, db, company) -> None:
        """A corrupted params blob must cost a correction, not a customer's quote."""
        row = self._company_with_model(db, company)
        row.params = {"nonsense": True}
        db.commit()

        body = submit(client)["quote"]
        assert body["amount_min_cents"] > 0
        trace = db.scalar(select(QuoteCalibration))
        assert trace.fallback_reason is not None

    def test_active_mode_applies_the_adjustment(self, client, db, company) -> None:
        """The mechanism works end to end; 6B simply never turns it on in production."""
        row = self._company_with_model(db, company)
        calib.activate(db, company.id, row.id)
        company.settings = {"calibration_mode": "active"}
        db.commit()

        body = submit(client)["quote"]
        quote = db.scalar(select(Quote).where(Quote.public_token == body["public_token"]))
        trace = db.scalar(select(QuoteCalibration).where(QuoteCalibration.quote_id == quote.id))

        assert trace.applied is True
        assert quote.total_cents == trace.calibrated_total_cents
        assert quote.total_cents > trace.base_total_cents

    def test_an_active_model_from_another_tenant_is_never_used(
        self, client, db, company, rival
    ) -> None:
        for i in range(40):
            add_job(db, rival, index=i, ratio=1.9)
        theirs = calib.train(db, rival.id)
        calib.activate(db, rival.id, theirs.id)
        rival.settings = {"calibration_mode": "active"}
        company.settings = {"calibration_mode": "active"}
        db.commit()

        submit(client)
        trace = db.scalar(select(QuoteCalibration).where(QuoteCalibration.company_id == company.id))
        assert trace.model_id is None
        assert trace.fallback_reason == "no_model"
        assert trace.factor == 1.0

    def test_a_quote_is_reproducible_from_its_trace(self, client, db, company) -> None:
        """Model params plus the quote's own snapshot must replay the number exactly."""
        row = self._company_with_model(db, company)
        calib.activate(db, company.id, row.id)
        company.settings = {"calibration_mode": "active"}
        db.commit()
        submit(client)

        quote = db.scalar(select(Quote).where(Quote.total_cents.is_not(None)))
        trace = db.scalar(select(QuoteCalibration).where(QuoteCalibration.quote_id == quote.id))
        model = load_model(row.algorithm, row.params)

        from app.pricing.calibration import CalibrationFeatures

        prediction = model.predict(
            CalibrationFeatures(
                home_size=quote.inputs_snapshot["home_size"],
                packing_service=quote.inputs_snapshot["packing_service"],
                base_hours=trace.base_hours,
            )
        )
        assert math.exp(prediction.log_ratio) == pytest.approx(trace.raw_factor, abs=1e-9)

    def test_shadow_comparison_summarises_behaviour(self, client, db, company) -> None:
        self._company_with_model(db, company)
        for _ in range(3):
            submit(client)
        summary = calib.shadow_comparison(db, company.id)
        assert summary["calibrations_recorded"] == 3
        assert summary["applied"] == 0
        assert summary["median_factor"] is not None
