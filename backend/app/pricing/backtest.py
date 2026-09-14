"""Leakage-free evaluation of a calibration model against the rule engine.

Time-ordered, always. A random split would let a model see next summer while predicting
last spring, and seasonality plus config changes make that flattering rather than
informative. Every fold here trains strictly on moves that happened *before* the moves it
is tested on — the same information a model would have had in production on that day.

The harness reports three things and refuses to report any of them on too little data:
how the engine did, how the calibrated engine did, and whether the difference survives a
bootstrap interval. A point estimate alone is how teams convince themselves a model works.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.pricing.baseline import TrainingRow
from app.pricing.calibration.models import (
    MIN_COMPANY_MOVES,
    CalibrationFeatures,
    CalibrationModel,
)
from app.pricing.metrics import (
    MIN_SAMPLE_FOR_METRICS,
    HoursMetrics,
    Improvement,
    bootstrap_improvement,
    hours_metrics,
)

#: A fold must train on at least this many moves, or the model it fits is not the model
#: production would have used.
MIN_TRAIN_PER_FOLD = MIN_COMPANY_MOVES

#: And must test on enough to say anything.
MIN_TEST_PER_FOLD = MIN_SAMPLE_FOR_METRICS

DEFAULT_FOLDS = 4

ModelFitter = Callable[[list[TrainingRow]], CalibrationModel]


@dataclass(frozen=True)
class FoldResult:
    cutoff: str
    n_train: int
    n_test: int
    baseline: HoursMetrics
    calibrated: HoursMetrics


@dataclass(frozen=True)
class BacktestResult:
    """The whole evaluation. ``usable`` is false when there was not enough data."""

    algorithm: str
    n_rows: int
    n_folds: int
    usable: bool
    reason: str | None = None
    folds: list[FoldResult] = field(default_factory=list)
    baseline: HoursMetrics | None = None
    calibrated: HoursMetrics | None = None
    improvement: Improvement | None = None
    #: Share of test predictions the layer would have clamped. A high number means the
    #: model is fighting its own bounds and should not be promoted.
    clamp_rate_pct: float | None = None


def _predict_hours(
    model: CalibrationModel, row: TrainingRow, cap: float | None
) -> tuple[float, bool]:
    """Apply a model the way the layer would, including the clamp."""
    import math

    prediction = model.predict(
        CalibrationFeatures(
            home_size=row.home_size,
            packing_service=row.packing_service,
            base_hours=row.base_hours,
        )
    )
    raw = math.exp(prediction.log_ratio)
    factor = raw if cap is None else min(max(raw, 1 - cap), 1 + cap)
    return row.base_hours * factor, abs(factor - raw) > 1e-9


def rolling_origin_backtest(
    rows: Sequence[TrainingRow],
    fit: ModelFitter,
    *,
    algorithm: str,
    folds: int = DEFAULT_FOLDS,
    clamp_cap: Callable[[int], float | None] | None = None,
) -> BacktestResult:
    """Train on the past, test on the future, several times over.

    :param clamp_cap: the production clamp policy, applied during evaluation so the
        measured improvement is the one customers would actually have received — not an
        unbounded model's theoretical best.
    """
    ordered = sorted(rows, key=lambda r: (r.move_date, r.job_id))
    n = len(ordered)
    if n < MIN_TRAIN_PER_FOLD + MIN_TEST_PER_FOLD:
        return BacktestResult(
            algorithm=algorithm,
            n_rows=n,
            n_folds=0,
            usable=False,
            reason=(
                f"needs at least {MIN_TRAIN_PER_FOLD + MIN_TEST_PER_FOLD} usable moves, has {n}"
            ),
        )

    # Cutoffs evenly spaced through the tail, leaving the first block for training.
    first_cut = max(MIN_TRAIN_PER_FOLD, n // 2)
    step = max((n - first_cut) // folds, MIN_TEST_PER_FOLD)
    boundaries = list(range(first_cut, n, step))

    results: list[FoldResult] = []
    base_all: list[float] = []
    cal_all: list[float] = []
    act_all: list[float] = []
    clamped_count = 0

    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else n
        train, test = ordered[:start], ordered[start:end]
        if len(train) < MIN_TRAIN_PER_FOLD or len(test) < MIN_TEST_PER_FOLD:
            continue

        model = fit(list(train))
        cap = clamp_cap(model.n_train) if clamp_cap else None

        base_pred = [row.base_hours for row in test]
        cal_pred: list[float] = []
        for row in test:
            hours, was_clamped = _predict_hours(model, row, cap)
            cal_pred.append(hours)
            clamped_count += int(was_clamped)
        actual = [row.actual_hours for row in test]

        results.append(
            FoldResult(
                cutoff=test[0].move_date.isoformat(),
                n_train=len(train),
                n_test=len(test),
                baseline=hours_metrics(base_pred, actual),
                calibrated=hours_metrics(cal_pred, actual),
            )
        )
        base_all.extend(base_pred)
        cal_all.extend(cal_pred)
        act_all.extend(actual)

    if not results or len(act_all) < MIN_SAMPLE_FOR_METRICS:
        return BacktestResult(
            algorithm=algorithm,
            n_rows=n,
            n_folds=len(results),
            usable=False,
            reason="not enough out-of-sample moves across folds",
            folds=results,
        )

    return BacktestResult(
        algorithm=algorithm,
        n_rows=n,
        n_folds=len(results),
        usable=True,
        folds=results,
        baseline=hours_metrics(base_all, act_all),
        calibrated=hours_metrics(cal_all, act_all),
        improvement=bootstrap_improvement(base_all, cal_all, act_all),
        clamp_rate_pct=round(clamped_count / len(act_all) * 100, 1),
    )


def shuffled_label_control(
    rows: Sequence[TrainingRow],
    fit: ModelFitter,
    *,
    algorithm: str,
    seed: int = 99,
) -> BacktestResult:
    """The same backtest with the targets permuted.

    If a model still "improves" after its labels are scrambled, the improvement is not
    learning — it is leakage, or a bug in this harness. This control is cheap and it is
    the single most useful test in the whole evaluation stack.
    """
    rng = random.Random(seed)
    ordered = sorted(rows, key=lambda r: (r.move_date, r.job_id))
    shuffled_actuals = [row.actual_hours for row in ordered]
    rng.shuffle(shuffled_actuals)

    import math

    scrambled = [
        TrainingRow(
            job_id=row.job_id,
            move_date=row.move_date,
            company_id=row.company_id,
            source=row.source,
            home_size=row.home_size,
            packing_service=row.packing_service,
            base_hours=row.base_hours,
            base_total_cents=row.base_total_cents,
            base_crew_size=row.base_crew_size,
            actual_hours=actual,
            log_ratio=math.log(actual / row.base_hours),
            distance_miles=row.distance_miles,
            distance_derived=row.distance_derived,
        )
        for row, actual in zip(ordered, shuffled_actuals, strict=True)
    ]
    return rolling_origin_backtest(scrambled, fit, algorithm=f"{algorithm}__shuffled")
