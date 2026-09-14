"""Deriving distances for historical moves that were exported without mileage.

The rule, from the Step 6 decisions: **derive when we safely can, never guess.** A move
with both postcodes gets a real provider lookup, cached forever. A move missing either
postcode gets nothing — it stays fully usable for analytics and similarity, and is simply
not eligible for any calibration method that needs an engine baseline.

Lookups are batched by distinct pair, because a thousand imported moves between the same
few suburbs are a handful of distinct pairs, not a thousand API calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Job, ZipDistance
from app.providers.distance import DistanceProvider, RoutePoint

logger = get_logger(__name__)

#: Ceiling on provider calls triggered by a single resolve pass. A company importing a
#: decade of history should not be able to fire thousands of billable lookups at once;
#: the remainder resolve on the next pass.
MAX_LOOKUPS_PER_PASS = 200


@dataclass(frozen=True)
class ZipPair:
    origin: str
    destination: str


def _normalize(value: str | None) -> str | None:
    """Postcodes vary in spacing and case across exports; the cache key must not."""
    if not value:
        return None
    cleaned = "".join(value.split()).upper()
    return cleaned or None


def pair_for_job(job: Job) -> ZipPair | None:
    origin, destination = _normalize(job.origin_zip), _normalize(job.destination_zip)
    if origin is None or destination is None:
        return None
    return ZipPair(origin=origin, destination=destination)


def cached_distances(db: Session, pairs: set[ZipPair]) -> dict[ZipPair, float | None]:
    """Everything already known, including remembered failures."""
    if not pairs:
        return {}
    keys = [(p.origin, p.destination) for p in pairs]
    rows = db.scalars(
        select(ZipDistance).where(
            tuple_(ZipDistance.origin_zip, ZipDistance.destination_zip).in_(keys)
        )
    ).all()
    return {ZipPair(r.origin_zip, r.destination_zip): r.miles for r in rows}


def resolve_distances(
    db: Session,
    pairs: set[ZipPair],
    provider: DistanceProvider,
    *,
    max_lookups: int = MAX_LOOKUPS_PER_PASS,
) -> dict[ZipPair, float | None]:
    """Return miles for every pair, calling the provider only for unknown ones.

    A failed lookup is cached as ``None`` so a permanently unresolvable pair costs one
    call, not one per training run.
    """
    known = cached_distances(db, pairs)
    unknown = [p for p in pairs if p not in known]

    for pair in unknown[:max_lookups]:
        miles: float | None = None
        name = "unresolved"
        try:
            # ZIP-only routing: the provider is given the postcode alone, which is all a
            # historical row has. Street-level precision is neither available nor needed
            # for a distance band the engine turns into a travel fee.
            result = provider.distance_miles(
                RoutePoint(line1="", city="", state="", zip=pair.origin),
                RoutePoint(line1="", city="", state="", zip=pair.destination),
            )
            miles, name = result.miles, result.provider
        except Exception:
            logger.warning("Distance lookup failed for %s→%s", pair.origin, pair.destination)

        db.add(
            ZipDistance(
                origin_zip=pair.origin,
                destination_zip=pair.destination,
                miles=miles,
                provider=name,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # Another pass resolved the same pair concurrently; its answer is as good.
            db.rollback()
        known[pair] = miles

    if len(unknown) > max_lookups:
        logger.info(
            "Deferred %d distance lookups to a later pass", len(unknown) - max_lookups
        )
    return known


def distances_for_jobs(
    db: Session, jobs: list[Job], provider: DistanceProvider
) -> dict[ZipPair, float | None]:
    """Resolve every distinct ZIP pair needed by a set of jobs, in one batch."""
    needed = {
        pair
        for job in jobs
        if job.distance_miles is None and (pair := pair_for_job(job)) is not None
    }
    return resolve_distances(db, needed, provider)


def jobs_missing_distance(db: Session, company_id: uuid.UUID) -> int:
    """How many of a company's moves still have no usable distance."""
    return len(
        db.scalars(
            select(Job.id).where(
                Job.company_id == company_id,
                Job.distance_miles.is_(None),
                or_(Job.origin_zip.is_(None), Job.destination_zip.is_(None)),
            )
        ).all()
    )
