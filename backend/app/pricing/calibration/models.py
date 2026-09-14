"""Calibration models. Two of them, both deliberately simple.

The target is ``log(actual_hours / engine_hours)``: how far the rule engine is off, in
proportional terms. Predicting the *residual* rather than hours outright means the model
only has to learn what the rules get wrong, which is learnable from tens of moves rather
than tens of thousands.

Medians, not means. One mis-keyed 30-hour move should not move a company's correction,
and in a dataset assembled from spreadsheets there will be mis-keyed rows.

Neither model is a black box: ``GlobalScalarModel`` is one number and ``SegmentedModel``
is one number per home size, shrunk toward the global one. A company owner can be shown
the actual factor and told what it means, which matters more than the last percent of
accuracy when someone is deciding whether to trust their prices to it.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Protocol

#: Below this many usable historical moves, a company gets no calibration at all.
MIN_COMPANY_MOVES = 20

#: A segment needs this much support before its own factor is used instead of the
#: company-wide one. Below it the segment median is mostly noise.
MIN_SEGMENT_SUPPORT = 8

#: Shrinkage strength: a segment with this many rows is weighted equally against the
#: global factor. Small segments therefore stay close to the company average instead of
#: chasing three unusual moves.
SHRINKAGE_K = 10.0


@dataclass(frozen=True)
class CalibrationFeatures:
    """What a model may look at. Every field is known at quote time, by construction."""

    home_size: str
    packing_service: str
    base_hours: float


@dataclass(frozen=True)
class Prediction:
    """A model's raw opinion, before the layer decides whether to trust it."""

    log_ratio: float
    support: int
    segment: str | None = None

    @property
    def factor(self) -> float:
        return math.exp(self.log_ratio)


class CalibrationModel(Protocol):
    """Everything the layer needs from a calibration model.

    Both attributes are read-only: models are frozen value objects, so a fitted model
    cannot be mutated after the fact and a stored quote's trace always describes the
    model that actually priced it.
    """

    @property
    def algorithm(self) -> str: ...

    @property
    def n_train(self) -> int: ...

    def predict(self, features: CalibrationFeatures) -> Prediction: ...

    def to_params(self) -> dict[str, Any]: ...


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


@dataclass(frozen=True)
class GlobalScalarModel:
    """One correction factor for the whole company.

    Fixes systematic bias — "this company's moves run 12% longer than the rules say" —
    and nothing else. It is usually where most of the available improvement lives, and it
    is the only thing that can be fitted responsibly from twenty moves.
    """

    algorithm = "global_scalar"

    log_ratio: float
    n_train: int

    @classmethod
    def fit(cls, log_ratios: list[float]) -> GlobalScalarModel:
        return cls(log_ratio=_median(log_ratios), n_train=len(log_ratios))

    def predict(self, features: CalibrationFeatures) -> Prediction:
        return Prediction(log_ratio=self.log_ratio, support=self.n_train, segment="all")

    def to_params(self) -> dict[str, Any]:
        return {"log_ratio": self.log_ratio, "n_train": self.n_train}

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> GlobalScalarModel:
        return cls(log_ratio=float(params["log_ratio"]), n_train=int(params["n_train"]))


@dataclass(frozen=True)
class SegmentedModel:
    """A factor per home size, shrunk toward the company-wide factor.

    Home size is the only segmentation in V1 because it is the strongest driver of
    duration and the one dimension where a company's history reliably has rows in every
    bucket. Adding packing or access segments before the data supports them would split
    an already small sample into pieces too thin to fit.
    """

    algorithm = "segmented_home_size"

    global_log_ratio: float
    segments: dict[str, dict[str, float]]
    n_train: int
    shrinkage_k: float = SHRINKAGE_K

    @classmethod
    def fit(
        cls, rows: list[tuple[str, float]], *, shrinkage_k: float = SHRINKAGE_K
    ) -> SegmentedModel:
        """Fit from ``(home_size, log_ratio)`` pairs."""
        overall = _median([value for _, value in rows])
        by_segment: dict[str, list[float]] = {}
        for segment, value in rows:
            by_segment.setdefault(segment, []).append(value)

        segments: dict[str, dict[str, float]] = {}
        for segment, values in by_segment.items():
            n = len(values)
            raw = _median(values)
            # Shrink toward the company factor in proportion to how little evidence the
            # segment has. A 3-row segment barely moves; a 50-row segment nearly owns it.
            shrunk = (n * raw + shrinkage_k * overall) / (n + shrinkage_k)
            segments[segment] = {"log_ratio": shrunk, "raw_log_ratio": raw, "n": float(n)}

        return cls(
            global_log_ratio=overall,
            segments=segments,
            n_train=len(rows),
            shrinkage_k=shrinkage_k,
        )

    def predict(self, features: CalibrationFeatures) -> Prediction:
        segment = self.segments.get(features.home_size)
        if segment is None or segment["n"] < MIN_SEGMENT_SUPPORT:
            # Not enough rows like this move: fall back to the company-wide factor rather
            # than trust a median of three.
            return Prediction(
                log_ratio=self.global_log_ratio, support=self.n_train, segment="all"
            )
        return Prediction(
            log_ratio=segment["log_ratio"],
            support=int(segment["n"]),
            segment=features.home_size,
        )

    def to_params(self) -> dict[str, Any]:
        return {
            "global_log_ratio": self.global_log_ratio,
            "segments": self.segments,
            "n_train": self.n_train,
            "shrinkage_k": self.shrinkage_k,
        }

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> SegmentedModel:
        return cls(
            global_log_ratio=float(params["global_log_ratio"]),
            segments={k: dict(v) for k, v in params["segments"].items()},
            n_train=int(params["n_train"]),
            shrinkage_k=float(params.get("shrinkage_k", SHRINKAGE_K)),
        )


_REGISTRY: dict[str, Any] = {
    GlobalScalarModel.algorithm: GlobalScalarModel,
    SegmentedModel.algorithm: SegmentedModel,
}


def build_model(algorithm: str, rows: list[tuple[str, float]]) -> CalibrationModel:
    """Fit the named algorithm from ``(home_size, log_ratio)`` pairs."""
    if algorithm == GlobalScalarModel.algorithm:
        return GlobalScalarModel.fit([value for _, value in rows])
    if algorithm == SegmentedModel.algorithm:
        return SegmentedModel.fit(rows)
    raise ValueError(f"Unknown calibration algorithm: {algorithm!r}")


def load_model(algorithm: str, params: dict[str, Any]) -> CalibrationModel:
    """Rebuild a stored model. This is what makes an old quote reproducible."""
    cls = _REGISTRY.get(algorithm)
    if cls is None:
        raise ValueError(f"Unknown calibration algorithm: {algorithm!r}")
    model: CalibrationModel = cls.from_params(params)
    return model
