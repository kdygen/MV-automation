"""Pricing service: config versioning and estimate computation.

Bridges storage and the pure pricing package. Companies get a default config seeded on
first use so instant quotes work out of the box; edits create new versions (append-only)
and deactivate the old row in the same transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import MovingRequest, PricingConfigRow
from app.pricing import Estimate, MoveSpec, PricingConfig, RuleBasedEngine

logger = get_logger(__name__)

_engine = RuleBasedEngine()


def get_active_config_row(db: Session, company_id: uuid.UUID) -> PricingConfigRow | None:
    """Return the company's active pricing config row, if any."""
    return db.scalar(
        select(PricingConfigRow).where(
            PricingConfigRow.company_id == company_id,
            PricingConfigRow.is_active.is_(True),
        )
    )


def ensure_active_config(db: Session, company_id: uuid.UUID) -> PricingConfigRow:
    """Return the active config row, seeding version 1 with defaults if none exists."""
    row = get_active_config_row(db, company_id)
    if row is not None:
        return row

    row = PricingConfigRow(
        company_id=company_id,
        version=1,
        is_active=True,
        config=PricingConfig().model_dump(mode="json"),
    )
    db.add(row)
    db.commit()
    logger.info("Seeded default pricing config v1 for company %s", company_id)
    return row


def set_config(db: Session, company_id: uuid.UUID, config: PricingConfig) -> PricingConfigRow:
    """Store ``config`` as a new active version; previous versions are kept, deactivated."""
    next_version = (
        db.scalar(
            select(func.max(PricingConfigRow.version)).where(
                PricingConfigRow.company_id == company_id
            )
        )
        or 0
    ) + 1

    db.execute(
        update(PricingConfigRow)
        .where(PricingConfigRow.company_id == company_id, PricingConfigRow.is_active.is_(True))
        .values(is_active=False)
    )
    row = PricingConfigRow(
        company_id=company_id,
        version=next_version,
        is_active=True,
        config=config.model_dump(mode="json"),
    )
    db.add(row)
    db.commit()
    logger.info("Pricing config v%s activated for company %s", next_version, company_id)
    return row


def load_config(row: PricingConfigRow) -> PricingConfig:
    """Parse a stored config row back into a validated :class:`PricingConfig`."""
    return PricingConfig.model_validate(row.config)


def estimate_for_request(
    db: Session, request: MovingRequest
) -> tuple[Estimate, PricingConfigRow]:
    """Price a moving request with its company's active config.

    :raises app.pricing.PricingInputError: if the request cannot be priced yet
        (e.g. distance unknown) — callers park the request for review instead of failing.
    """
    config_row = ensure_active_config(db, request.company_id)
    spec = MoveSpec.from_moving_request(request)
    estimate = _engine.estimate(spec, load_config(config_row))
    return estimate, config_row
