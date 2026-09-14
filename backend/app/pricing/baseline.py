"""Recomputing *our* engine's estimate for a historical move.

Calibration learns how wrong ``RuleBasedEngine`` is, which means every training row needs
an estimate produced by that engine. For a platform-completed move we already have one.
For an imported move we do not — and the ``quoted_hours`` in a legacy export is the
*company's previous system's* number, not ours. Training against that would teach us
their bias instead of our own, so the baseline is always recomputed here.

Eligibility is strict on purpose. A row only trains if the engine can price it from
features a future customer would also supply, which is what keeps the residual an
honest measure of engine error rather than a measure of what the export forgot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models import Job
from app.pricing.config import PricingConfig
from app.pricing.engine import Estimate, PricingInputError
from app.pricing.rules import RuleBasedEngine
from app.pricing.spec import MoveSpec, SideAccess

_engine = RuleBasedEngine()

#: Ratios outside this band are data errors, not evidence. A move that took a quarter of
#: the estimate or four times it is a mis-keyed hour, a wrong unit, or a mis-mapped
#: column — including it would move the correction far more than any real signal.
MIN_PLAUSIBLE_RATIO = 0.25
MAX_PLAUSIBLE_RATIO = 4.0

#: Hours outside this band cannot be a real local move.
MIN_PLAUSIBLE_HOURS = 0.5
MAX_PLAUSIBLE_HOURS = 48.0


class Ineligible(str):
    """Why a historical move cannot be used for calibration. Reported, never silent."""


NO_ACTUAL_HOURS = Ineligible("no_actual_hours")
IMPLAUSIBLE_HOURS = Ineligible("implausible_hours")
NO_DISTANCE = Ineligible("no_distance")
NO_ACCESS = Ineligible("no_access")
UNPRICEABLE = Ineligible("engine_cannot_price")
IMPLAUSIBLE_RATIO = Ineligible("implausible_ratio")


@dataclass(frozen=True)
class TrainingRow:
    """One historical move with our engine's own estimate beside its outcome."""

    job_id: str
    move_date: date
    company_id: str
    source: str
    home_size: str
    packing_service: str
    base_hours: float
    base_total_cents: int
    base_crew_size: int
    actual_hours: float
    #: ``log(actual / base)`` — the V1 prediction target.
    log_ratio: float
    distance_miles: float
    distance_derived: bool

    @property
    def ratio(self) -> float:
        return self.actual_hours / self.base_hours


@dataclass(frozen=True)
class Excluded:
    job_id: str
    reason: str


def has_access_information(job: Job) -> bool:
    """Whether the origin's access was actually recorded.

    Required, and the reason is subtle. ``SideAccess`` defaults to a ground-floor,
    no-stairs move, so a row with unknown access is priced as if it were easy. If a
    company's history is mostly third-floor walk-ups that nobody exported, the engine
    under-predicts them all and the calibration learns a large positive factor that is
    really "we were not told about stairs". Applying that factor to a *new* quote — one
    that does record stairs, because our intake form requires them — would count the
    same stairs twice.

    Requiring access on the origin keeps train-time and quote-time feature availability
    comparable. Platform completions always pass; sparse imports do not, and are honestly
    reported as ineligible rather than quietly skewing the correction.
    """
    return job.origin_floor is not None or job.origin_stairs_flights is not None


def spec_for_job(job: Job, *, distance_miles: float | None) -> MoveSpec | None:
    """Build the engine's input from a historical move, using only legal features."""
    if distance_miles is None:
        return None
    return MoveSpec(
        home_size=job.home_size,
        packing_service=job.packing_service,
        special_items=tuple(str(i) for i in (job.special_items or [])),
        origin=SideAccess(
            floor=max(job.origin_floor or 1, 1),
            has_elevator=bool(job.origin_has_elevator),
            stairs_flights=max(job.origin_stairs_flights or 0, 0),
        ),
        destination=SideAccess(
            floor=max(job.destination_floor or 1, 1),
            has_elevator=bool(job.destination_has_elevator),
            stairs_flights=max(job.destination_stairs_flights or 0, 0),
        ),
        distance_miles=distance_miles,
        move_date=job.move_date,
    )


def baseline_estimate(
    job: Job, config: PricingConfig, *, distance_miles: float | None
) -> Estimate | None:
    """Our engine's estimate for this historical move, or ``None`` if unpriceable."""
    spec = spec_for_job(job, distance_miles=distance_miles)
    if spec is None:
        return None
    try:
        return _engine.estimate(spec, config)
    except PricingInputError:
        return None


def training_row(
    job: Job, config: PricingConfig, *, distance_miles: float | None
) -> TrainingRow | Excluded:
    """Turn one historical move into a training row, or say precisely why not."""
    import math

    job_id = str(job.id)

    if job.actual_hours is None:
        return Excluded(job_id, NO_ACTUAL_HOURS)
    if not MIN_PLAUSIBLE_HOURS <= job.actual_hours <= MAX_PLAUSIBLE_HOURS:
        return Excluded(job_id, IMPLAUSIBLE_HOURS)

    derived = job.distance_miles is None
    miles = job.distance_miles if job.distance_miles is not None else distance_miles
    if miles is None:
        return Excluded(job_id, NO_DISTANCE)
    if not has_access_information(job):
        return Excluded(job_id, NO_ACCESS)

    estimate = baseline_estimate(job, config, distance_miles=miles)
    if estimate is None or estimate.estimated_hours <= 0:
        return Excluded(job_id, UNPRICEABLE)

    ratio = job.actual_hours / estimate.estimated_hours
    if not MIN_PLAUSIBLE_RATIO <= ratio <= MAX_PLAUSIBLE_RATIO:
        return Excluded(job_id, IMPLAUSIBLE_RATIO)

    return TrainingRow(
        job_id=job_id,
        move_date=job.move_date,
        company_id=str(job.company_id),
        source=job.source.value if hasattr(job.source, "value") else str(job.source),
        home_size=job.home_size.value if hasattr(job.home_size, "value") else str(job.home_size),
        packing_service=(
            job.packing_service.value
            if hasattr(job.packing_service, "value")
            else str(job.packing_service)
        ),
        base_hours=estimate.estimated_hours,
        base_total_cents=estimate.total_cents,
        base_crew_size=estimate.crew_size,
        actual_hours=job.actual_hours,
        log_ratio=math.log(ratio),
        distance_miles=miles,
        distance_derived=derived,
    )
