"""Accuracy metrics for hour estimates, and the rules about when not to report one.

Medians throughout rather than means: a single mis-keyed 40-hour move would drag a mean
badly, and the whole point of these numbers is to notice bad data, not be fooled by it.

Two numbers matter most and are often confused. **Median APE** says how far off we are;
**median signed error** says in which direction. A company can be 20% off with no bias
(noisy) or 20% off entirely in one direction (correctable). Only the second is something
a scalar correction can fix, so both are always reported together.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass

#: Below this many observations a metric is noise dressed as a number. Reporting
#: "improved 30%" on six moves would be worse than reporting nothing.
MIN_SAMPLE_FOR_METRICS = 10


@dataclass(frozen=True)
class HoursMetrics:
    """How well a set of predictions matched what actually happened."""

    n: int
    median_ape_pct: float | None = None
    mae_hours: float | None = None
    #: Signed. Positive means predictions ran low — the job took longer than predicted.
    median_signed_error_pct: float | None = None
    p90_abs_error_hours: float | None = None
    within_15_pct: float | None = None
    within_25_pct: float | None = None

    @property
    def reportable(self) -> bool:
        return self.n >= MIN_SAMPLE_FOR_METRICS


def hours_metrics(predicted: list[float], actual: list[float]) -> HoursMetrics:
    """Compare predicted against actual hours. Lists must be aligned and non-empty."""
    if len(predicted) != len(actual):
        raise ValueError("predicted and actual must be the same length")
    pairs = [(p, a) for p, a in zip(predicted, actual, strict=True) if p > 0 and a > 0]
    if not pairs:
        return HoursMetrics(n=0)

    abs_errors = [abs(a - p) for p, a in pairs]
    signed_pct = [(a - p) / p * 100 for p, a in pairs]
    abs_pct = [abs(value) for value in signed_pct]
    ordered = sorted(abs_errors)
    # Nearest-rank P90: with small samples an interpolated quantile invents a value
    # between two observations, which reads as false precision.
    p90 = ordered[min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)]

    return HoursMetrics(
        n=len(pairs),
        median_ape_pct=round(statistics.median(abs_pct), 2),
        mae_hours=round(statistics.fmean(abs_errors), 3),
        median_signed_error_pct=round(statistics.median(signed_pct), 2),
        p90_abs_error_hours=round(p90, 3),
        within_15_pct=round(sum(1 for v in abs_pct if v <= 15) / len(pairs) * 100, 1),
        within_25_pct=round(sum(1 for v in abs_pct if v <= 25) / len(pairs) * 100, 1),
    )


@dataclass(frozen=True)
class Improvement:
    """Change from a baseline to a candidate, with an interval around it.

    The interval is the point of this type. A 12% improvement whose confidence interval
    spans zero is not an improvement, and promoting on the point estimate alone is how
    teams ship models that do nothing.
    """

    baseline_median_ape: float
    candidate_median_ape: float
    relative_improvement_pct: float
    ci_low_pct: float
    ci_high_pct: float
    n: int

    @property
    def significant(self) -> bool:
        """True when the whole interval sits on the improving side of zero."""
        return self.ci_low_pct > 0


def bootstrap_improvement(
    baseline_pred: list[float],
    candidate_pred: list[float],
    actual: list[float],
    *,
    iterations: int = 2000,
    seed: int = 12345,
) -> Improvement | None:
    """Relative reduction in median APE, with a bootstrap 95% interval.

    Seeded so a promotion decision is reproducible: re-running the evaluation must not
    change whether a model qualifies.
    """
    if not (len(baseline_pred) == len(candidate_pred) == len(actual)):
        raise ValueError("all three series must be the same length")
    n = len(actual)
    if n < MIN_SAMPLE_FOR_METRICS:
        return None

    def median_ape(pred: list[float], act: list[float]) -> float:
        return statistics.median(
            [abs(a - p) / p * 100 for p, a in zip(pred, act, strict=True) if p > 0]
        )

    base = median_ape(baseline_pred, actual)
    cand = median_ape(candidate_pred, actual)
    point = (base - cand) / base * 100 if base > 0 else 0.0

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        idx = [rng.randrange(n) for _ in range(n)]
        b = median_ape([baseline_pred[i] for i in idx], [actual[i] for i in idx])
        c = median_ape([candidate_pred[i] for i in idx], [actual[i] for i in idx])
        samples.append((b - c) / b * 100 if b > 0 else 0.0)
    samples.sort()

    return Improvement(
        baseline_median_ape=round(base, 2),
        candidate_median_ape=round(cand, 2),
        relative_improvement_pct=round(point, 2),
        ci_low_pct=round(samples[int(0.025 * iterations)], 2),
        ci_high_pct=round(samples[int(0.975 * iterations) - 1], 2),
        n=n,
    )
