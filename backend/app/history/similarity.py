"""Deterministic structured similarity between moves. No embeddings, no learning.

Given the features of a move, this scores how comparable each historical move is. It is
pure arithmetic over structured fields: the same inputs always produce the same ranking,
which is what makes a signal traceable to specific rows rather than to a model's mood.

Two exclusions are deliberate and load-bearing:

**Price is never a feature.** Finding comparable moves by price and then using those
moves to calibrate prices is circular — it would confirm whatever the old pricing already
did, including its mistakes.

**Crew size is not a feature either, by default.** Crew is an *output* we eventually want
to recommend, so matching on it would smuggle the answer into the question in exactly the
same way. It is available behind a flag for queries that are not about crew.

Each feature contributes a dissimilarity in ``[0, 1]`` times a weight, and the total is
normalized by the weight of the features **actually present on both moves**. That
renormalization is what stops a sparse historical row from being scored as a poor match
merely for being sparse, and stops one noisy field from dominating: a missing field
removes its weight instead of contributing an arbitrary 0 or 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.moving_request import HomeSize, PackingService

#: Ordinal scale for home size — the strongest single predictor of duration, so a 2BR is
#: much closer to a 1BR than to a 5BR rather than merely "different".
_HOME_SIZE_ORDER: tuple[str, ...] = (
    HomeSize.STUDIO.value,
    HomeSize.ONE_BR.value,
    HomeSize.TWO_BR.value,
    HomeSize.THREE_BR.value,
    HomeSize.FOUR_BR.value,
    HomeSize.FIVE_BR_PLUS.value,
)

_PACKING_ORDER: tuple[str, ...] = (
    PackingService.NONE.value,
    PackingService.PARTIAL.value,
    PackingService.FULL.value,
)

#: Miles of difference that count as "completely different trips". Local moves are the
#: MVP's scope, so 50 miles apart is already a different kind of job.
DISTANCE_SCALE = 50.0

#: Flights and floors beyond this are treated as maximally different.
STAIRS_SCALE = 5.0
FLOOR_SCALE = 5.0

#: Weights. Access carries real weight in total (30) because it is the largest driver of
#: hours after home size, but it is split across four signals so no single field decides.
WEIGHTS: dict[str, float] = {
    "home_size": 30.0,
    "distance_miles": 20.0,
    "stairs": 12.0,
    "floors": 10.0,
    "elevators": 8.0,
    "packing_service": 8.0,
    "special_items": 8.0,
    "geography": 4.0,
    "crew_size": 0.0,  # opt-in only; see the module docstring
}

#: A candidate must share at least this share of the total weight to be comparable at
#: all. Below it, a "match" would rest on one or two fields agreeing by chance.
MIN_COVERAGE = 0.5

TOTAL_WEIGHT = sum(w for name, w in WEIGHTS.items() if name != "crew_size")


@dataclass(frozen=True)
class MoveFeatures:
    """The structured description similarity compares. Deliberately price-free."""

    home_size: str | None = None
    distance_miles: float | None = None
    packing_service: str | None = None
    special_items: tuple[str, ...] | None = None
    origin_floor: int | None = None
    destination_floor: int | None = None
    origin_stairs_flights: int | None = None
    destination_stairs_flights: int | None = None
    origin_has_elevator: bool | None = None
    destination_has_elevator: bool | None = None
    origin_zip: str | None = None
    destination_zip: str | None = None
    origin_city: str | None = None
    destination_city: str | None = None
    origin_state: str | None = None
    destination_state: str | None = None
    crew_size: int | None = None

    @classmethod
    def from_job(cls, job: Any) -> MoveFeatures:
        """Read features off a ``Job`` row — platform-completed or imported alike.

        One reader for both provenances is what makes "the same intelligence path"
        literal rather than aspirational.
        """
        items = job.special_items
        return cls(
            home_size=enum_value(job.home_size),
            distance_miles=job.distance_miles,
            packing_service=enum_value(job.packing_service),
            special_items=_normalize_items(items) if items is not None else None,
            origin_floor=job.origin_floor,
            destination_floor=job.destination_floor,
            origin_stairs_flights=job.origin_stairs_flights,
            destination_stairs_flights=job.destination_stairs_flights,
            origin_has_elevator=job.origin_has_elevator,
            destination_has_elevator=job.destination_has_elevator,
            origin_zip=job.origin_zip,
            destination_zip=job.destination_zip,
            origin_city=job.origin_city,
            destination_city=job.destination_city,
            origin_state=job.origin_state,
            destination_state=job.destination_state,
            crew_size=job.actual_crew_size,
        )


def enum_value(value: Any) -> str | None:
    """Read an enum's value, tolerating a plain string.

    Rows arrive as enums when loaded through the ORM but as strings when freshly
    constructed and not yet round-tripped, so every read of an enum column goes through
    here rather than assuming ``.value`` exists.
    """
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _normalize_items(items: Any) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip().lower() for item in (items or []) if str(item).strip()}))


@dataclass(frozen=True)
class FeatureScore:
    """One feature's contribution, kept so a score can be explained rather than trusted."""

    name: str
    dissimilarity: float
    weight: float


@dataclass(frozen=True)
class Comparison:
    """The outcome of comparing two moves."""

    score: float
    coverage: float
    comparable: bool
    features: tuple[FeatureScore, ...] = field(default_factory=tuple)

    @property
    def matched_features(self) -> tuple[str, ...]:
        """Features that agreed closely — the human-readable "why this matched"."""
        return tuple(f.name for f in self.features if f.dissimilarity <= 0.25)


def _scaled_gap(a: float, b: float, scale: float) -> float:
    return min(abs(a - b) / scale, 1.0)


def _ordinal(a: str, b: str, order: tuple[str, ...]) -> float | None:
    if a not in order or b not in order:
        return None
    span = len(order) - 1
    return abs(order.index(a) - order.index(b)) / span if span else 0.0


def _pair(
    left: tuple[Any, Any], right: tuple[Any, Any], compare: Any
) -> float | None:
    """Average a paired feature (origin + destination) over whichever ends are known.

    A move that records only origin access still contributes that end rather than being
    skipped, which matters because origin access is the end most often recorded.
    """
    parts = [
        compare(a, b)
        for a, b in zip(left, right, strict=True)
        if a is not None and b is not None
    ]
    return sum(parts) / len(parts) if parts else None


def _geography(query: MoveFeatures, other: MoveFeatures) -> float | None:
    """Coarse locality: same ZIP, same city, same state, or elsewhere."""
    for attr, same in (("zip", 0.0), ("city", 0.3), ("state", 0.6)):
        q_origin = getattr(query, f"origin_{attr}")
        o_origin = getattr(other, f"origin_{attr}")
        if q_origin and o_origin:
            return same if str(q_origin).lower() == str(o_origin).lower() else 1.0
    return None


def _jaccard(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    left, right = set(a), set(b)
    if not left and not right:
        return 0.0  # both known to have none: they agree
    union = left | right
    return 1.0 - len(left & right) / len(union) if union else 0.0


def compare(
    query: MoveFeatures, other: MoveFeatures, *, include_crew: bool = False
) -> Comparison:
    """Score how comparable ``other`` is to ``query``.

    :param include_crew: allow crew size to influence the match. Leave it off for any
        question whose answer is a crew recommendation.
    """
    scores: list[FeatureScore] = []

    def add(name: str, dissimilarity: float | None) -> None:
        weight = WEIGHTS[name] if name != "crew_size" else (2.0 if include_crew else 0.0)
        if dissimilarity is None or weight <= 0:
            return
        scores.append(FeatureScore(name, min(max(dissimilarity, 0.0), 1.0), weight))

    if query.home_size and other.home_size:
        add("home_size", _ordinal(query.home_size, other.home_size, _HOME_SIZE_ORDER))
    if query.distance_miles is not None and other.distance_miles is not None:
        add(
            "distance_miles",
            _scaled_gap(query.distance_miles, other.distance_miles, DISTANCE_SCALE),
        )
    add(
        "stairs",
        _pair(
            (query.origin_stairs_flights, query.destination_stairs_flights),
            (other.origin_stairs_flights, other.destination_stairs_flights),
            lambda a, b: _scaled_gap(a, b, STAIRS_SCALE),
        ),
    )
    add(
        "floors",
        _pair(
            (query.origin_floor, query.destination_floor),
            (other.origin_floor, other.destination_floor),
            lambda a, b: _scaled_gap(a, b, FLOOR_SCALE),
        ),
    )
    add(
        "elevators",
        _pair(
            (query.origin_has_elevator, query.destination_has_elevator),
            (other.origin_has_elevator, other.destination_has_elevator),
            lambda a, b: 0.0 if a == b else 1.0,
        ),
    )
    if query.packing_service and other.packing_service:
        add(
            "packing_service",
            _ordinal(query.packing_service, other.packing_service, _PACKING_ORDER),
        )
    if query.special_items is not None and other.special_items is not None:
        add("special_items", _jaccard(query.special_items, other.special_items))
    add("geography", _geography(query, other))
    if include_crew and query.crew_size is not None and other.crew_size is not None:
        add("crew_size", _scaled_gap(query.crew_size, other.crew_size, 3.0))

    present = sum(f.weight for f in scores)
    if present <= 0:
        return Comparison(score=0.0, coverage=0.0, comparable=False)

    dissimilarity = sum(f.dissimilarity * f.weight for f in scores) / present
    coverage = min(present / TOTAL_WEIGHT, 1.0)
    return Comparison(
        score=round(1.0 - dissimilarity, 4),
        coverage=round(coverage, 4),
        comparable=coverage >= MIN_COVERAGE,
        features=tuple(scores),
    )
