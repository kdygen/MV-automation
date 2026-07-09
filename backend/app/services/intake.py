"""Intake service: turn a public form submission into a lead + structured moving request.

Responsibilities:
- resolve the tenant from its public slug,
- create the :class:`Lead` and :class:`MovingRequest` rows (one transaction),
- compute the driving distance via the configured :class:`DistanceProvider`,
- preserve the raw submission for auditability.

Distance failures are non-fatal: the request is stored with ``distance_miles=None`` and
can be quoted later once the distance is resolved — losing a lead over a maps outage
would be the wrong trade.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.models import Company, ExtractionSource, Lead, LeadSource, MovingRequest
from app.providers.distance import DistanceProvider, RoutePoint
from app.schemas.intake import MovingRequestIn

logger = get_logger(__name__)


def get_company_by_slug(db: Session, slug: str) -> Company:
    """Resolve a tenant from its public slug, or raise 404."""
    company = db.scalar(select(Company).where(Company.slug == slug))
    if company is None:
        raise NotFoundError("Company not found")
    return company


def create_moving_request(
    db: Session,
    *,
    company: Company,
    payload: MovingRequestIn,
    distance_provider: DistanceProvider,
    source: LeadSource = LeadSource.FORM,
    extracted_by: ExtractionSource = ExtractionSource.FORM,
) -> tuple[Lead, MovingRequest]:
    """Persist a lead and its structured moving request; returns both (committed)."""
    lead = Lead(
        company_id=company.id,
        name=payload.contact.name,
        email=payload.contact.email,
        phone=payload.contact.phone,
        source=source,
    )

    distance_miles: float | None = None
    try:
        result = distance_provider.distance_miles(
            RoutePoint(
                line1=payload.origin.line1,
                city=payload.origin.city,
                state=payload.origin.state,
                zip=payload.origin.zip,
            ),
            RoutePoint(
                line1=payload.destination.line1,
                city=payload.destination.city,
                state=payload.destination.state,
                zip=payload.destination.zip,
            ),
        )
        distance_miles = result.miles
    except Exception:  # pragma: no cover - exercised via unit test with a failing fake
        logger.exception("Distance provider failed; storing request without distance")

    db.add(lead)
    db.flush()  # assign lead.id before wiring the FK

    request = MovingRequest(
        company_id=company.id,
        lead_id=lead.id,
        origin_line1=payload.origin.line1,
        origin_city=payload.origin.city,
        origin_state=payload.origin.state,
        origin_zip=payload.origin.zip,
        origin_floor=payload.origin.floor,
        origin_has_elevator=payload.origin.has_elevator,
        origin_stairs_flights=payload.origin.stairs_flights,
        destination_line1=payload.destination.line1,
        destination_city=payload.destination.city,
        destination_state=payload.destination.state,
        destination_zip=payload.destination.zip,
        destination_floor=payload.destination.floor,
        destination_has_elevator=payload.destination.has_elevator,
        destination_stairs_flights=payload.destination.stairs_flights,
        move_date=payload.move_date,
        is_date_flexible=payload.is_date_flexible,
        home_size=payload.home_size,
        packing_service=payload.packing_service,
        special_items=payload.special_items,
        notes=payload.notes,
        distance_miles=distance_miles,
        extracted_by=extracted_by,
        raw_payload=payload.model_dump(mode="json"),
    )

    db.add(request)
    db.commit()

    logger.info(
        "Intake: lead=%s request=%s company=%s distance=%s",
        lead.id,
        request.id,
        company.slug,
        distance_miles,
    )
    return lead, request
