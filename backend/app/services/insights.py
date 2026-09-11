"""Historical signals: what comparable moves actually did.

The output of this module is **evidence**, never authority. It reports what happened on
moves like the one being asked about — durations, spread, how far estimates missed — and
stops there. It produces no price, no crew recommendation, and no aggregate money figure,
because the moment a number here looked like an answer somebody would wire it into a
quote without the calibration layer that decision deserves.

Tenant isolation happens **before** relevance: candidates are selected by
``company_id`` first, and scoring only ever sees rows that already belong to the caller.
A more similar move in another tenant is not a worse match, it is invisible.

Imported and platform-completed moves are read through one query and one feature reader,
so "the same intelligence path" is literal — a company that imports ten years of history
and then completes jobs on the platform gets one pooled evidence base.
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.history.similarity import (
    MIN_COVERAGE,
    MoveFeatures,
    compare,
    enum_value,
)
from app.models import Job
from app.models.moving_request import HomeSize
from app.schemas.history import HistoricalSignalsOut, SimilarMoveOut, SimilarQueryIn

logger = get_logger(__name__)

#: Ceiling on rows scored for one query. Scoring is cheap but not free, and a decade of
#: history is far more than the top ten comparables need. Bounded by recency so the
#: result stays deterministic for a given dataset.
MAX_CANDIDATES = 5000

#: How far from the queried home size a row may be and still be worth scoring. Two steps
#: covers 1BR-through-4BR against a 2BR query; beyond that the ordinal penalty alone
#: would rule it out.
HOME_SIZE_WINDOW = 2

_HOME_SIZES: tuple[str, ...] = (
    HomeSize.STUDIO.value,
    HomeSize.ONE_BR.value,
    HomeSize.TWO_BR.value,
    HomeSize.THREE_BR.value,
    HomeSize.FOUR_BR.value,
    HomeSize.FIVE_BR_PLUS.value,
)


def _features_from_query(query: SimilarQueryIn) -> MoveFeatures:
    return MoveFeatures(
        home_size=query.home_size.value if query.home_size else None,
        distance_miles=query.distance_miles,
        packing_service=query.packing_service.value if query.packing_service else None,
        special_items=(
            tuple(sorted({i.strip().lower() for i in query.special_items if i.strip()}))
            if query.special_items is not None
            else None
        ),
        origin_floor=query.origin_floor,
        destination_floor=query.destination_floor,
        origin_stairs_flights=query.origin_stairs_flights,
        destination_stairs_flights=query.destination_stairs_flights,
        origin_has_elevator=query.origin_has_elevator,
        destination_has_elevator=query.destination_has_elevator,
        origin_zip=query.origin_zip,
        destination_zip=query.destination_zip,
        origin_city=query.origin_city,
        destination_city=query.destination_city,
        origin_state=query.origin_state,
        destination_state=query.destination_state,
    )


def _candidates(db: Session, company_id: uuid.UUID, query: SimilarQueryIn) -> list[Job]:
    """This company's scoreable history, narrowed cheaply before any scoring.

    The tenant predicate is the first clause, not a filter applied afterwards: there is
    no moment at which a row from another company is a candidate.
    """
    statement = select(Job).where(Job.company_id == company_id)

    if query.exclude_move_id is not None:
        statement = statement.where(Job.id != query.exclude_move_id)

    if query.home_size is not None:
        index = _HOME_SIZES.index(query.home_size.value)
        window = _HOME_SIZES[
            max(index - HOME_SIZE_WINDOW, 0) : index + HOME_SIZE_WINDOW + 1
        ]
        statement = statement.where(Job.home_size.in_(window))

    # Deterministic ordering so the candidate cap always takes the same rows.
    statement = statement.order_by(Job.move_date.desc(), Job.id).limit(MAX_CANDIDATES)
    return list(db.scalars(statement))


def _hours_error_pct(job: Job) -> float | None:
    if job.actual_hours is None or not job.quoted_hours:
        return None
    return round((job.actual_hours - job.quoted_hours) / job.quoted_hours * 100, 2)


def _match_out(
    job: Job, score: float, coverage: float, matched_on: tuple[str, ...]
) -> SimilarMoveOut:
    """Build one comparable. Street addresses are never included, by rule."""
    return SimilarMoveOut(
        id=job.id,
        source=job.source.value,
        score=score,
        coverage=coverage,
        matched_on=list(matched_on),
        move_date=job.move_date,
        home_size=enum_value(job.home_size) or "",
        distance_miles=job.distance_miles,
        packing_service=enum_value(job.packing_service),
        special_items=job.special_items,
        origin_floor=job.origin_floor,
        origin_stairs_flights=job.origin_stairs_flights,
        origin_has_elevator=job.origin_has_elevator,
        destination_floor=job.destination_floor,
        destination_stairs_flights=job.destination_stairs_flights,
        destination_has_elevator=job.destination_has_elevator,
        long_carry=job.long_carry,
        parking_difficulty=enum_value(job.parking_difficulty),
        actual_hours=job.actual_hours,
        actual_crew_size=job.actual_crew_size,
        quoted_hours=job.quoted_hours,
        hours_error_pct=_hours_error_pct(job),
        delay_minutes=job.delay_minutes,
        issue_tags=job.issue_tags,
        problem_notes=job.problem_notes,
        building_notes=job.building_notes,
    )


def similar_moves(
    db: Session,
    company_id: uuid.UUID,
    query: SimilarQueryIn,
    *,
    include_crew: bool = False,
) -> HistoricalSignalsOut:
    """Find comparable historical moves and summarize what they did.

    Ranking is fully deterministic: by score descending, then most recent, then id — so
    equal-scoring moves never swap places between two identical calls.
    """
    wanted = _features_from_query(query)
    scored: list[tuple[float, Job, float, tuple[str, ...]]] = []

    for job in _candidates(db, company_id, query):
        result = compare(wanted, MoveFeatures.from_job(job), include_crew=include_crew)
        if not result.comparable:
            continue
        scored.append((result.score, job, result.coverage, result.matched_features))

    scored.sort(key=lambda row: (-row[0], -row[1].move_date.toordinal(), str(row[1].id)))
    top = scored[: query.limit]

    matches = [_match_out(job, score, coverage, why) for score, job, coverage, why in top]
    hours = [m.actual_hours for m in matches if m.actual_hours is not None]
    errors = [m.hours_error_pct for m in matches if m.hours_error_pct is not None]

    quartiles: tuple[float | None, float | None] = (None, None)
    if len(hours) >= 4:
        # quantiles() needs at least two points per cut; below four the "middle half" is
        # not a meaningful statement about spread, so it is withheld rather than faked.
        cuts = statistics.quantiles(hours, n=4, method="inclusive")
        quartiles = (round(cuts[0], 2), round(cuts[2], 2))
    elif hours:
        quartiles = (round(min(hours), 2), round(max(hours), 2))

    tags = Counter(
        str(tag).strip().lower()
        for m in matches
        for tag in (m.issue_tags or [])
        if str(tag).strip()
    )

    signals = HistoricalSignalsOut(
        comparable_count=len(scored),
        moves_with_hours=len(hours),
        moves_with_estimate=len(errors),
        median_actual_hours=round(statistics.median(hours), 2) if hours else None,
        mean_actual_hours=round(statistics.fmean(hours), 2) if hours else None,
        hours_p25=quartiles[0],
        hours_p75=quartiles[1],
        median_hours_error_pct=round(statistics.median(errors), 2) if errors else None,
        overrun_share_pct=(
            round(sum(1 for e in errors if e > 0) / len(errors) * 100, 1) if errors else None
        ),
        common_issue_tags=[tag for tag, _ in tags.most_common(5)],
        matches=matches,
    )
    logger.info(
        "Similarity for company %s: %d comparable of %d scored (coverage floor %.0f%%)",
        company_id,
        len(scored),
        len(scored),
        MIN_COVERAGE * 100,
    )
    return signals


def similar_to_move(
    db: Session, company_id: uuid.UUID, move_id: uuid.UUID, *, limit: int = 10
) -> HistoricalSignalsOut:
    """Comparables for a move already in history — the dashboard's validation tool.

    Reads the subject through the same tenant-scoped lookup as everything else, so a
    move id from another company finds nothing rather than leaking its features.
    """
    subject = db.scalar(select(Job).where(Job.id == move_id, Job.company_id == company_id))
    if subject is None:
        raise NotFoundError("Historical move not found")

    features = MoveFeatures.from_job(subject)
    query = SimilarQueryIn(
        home_size=HomeSize(features.home_size) if features.home_size else None,
        distance_miles=features.distance_miles,
        packing_service=subject.packing_service,
        special_items=list(features.special_items) if features.special_items is not None else None,
        origin_floor=features.origin_floor,
        destination_floor=features.destination_floor,
        origin_stairs_flights=features.origin_stairs_flights,
        destination_stairs_flights=features.destination_stairs_flights,
        origin_has_elevator=features.origin_has_elevator,
        destination_has_elevator=features.destination_has_elevator,
        origin_zip=features.origin_zip,
        destination_zip=features.destination_zip,
        origin_city=features.origin_city,
        destination_city=features.destination_city,
        origin_state=features.origin_state,
        destination_state=features.destination_state,
        exclude_move_id=move_id,
        limit=limit,
    )
    return similar_moves(db, company_id, query)
