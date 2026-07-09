"""Authenticated settings endpoints: company profile and pricing configuration.

Reads are open to all staff; writes require owner/admin. Pricing writes create a new
config version (append-only) so historical quotes keep their audit trail.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.db.session import get_db
from app.models import UserRole
from app.schemas.dashboard import (
    CompanySettingsIn,
    CompanySettingsOut,
    PricingSettingsIn,
    PricingSettingsOut,
)
from app.services import dashboard
from app.services import pricing as pricing_service

router = APIRouter(prefix="/settings", tags=["settings"])

_admin_only = require_roles(UserRole.OWNER, UserRole.ADMIN)


@router.get("/company", response_model=CompanySettingsOut)
def get_company_settings(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompanySettingsOut:
    return dashboard.get_company_settings(db, user.company_id)


@router.patch("/company", response_model=CompanySettingsOut)
def update_company_settings(
    payload: CompanySettingsIn,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> CompanySettingsOut:
    return dashboard.update_company_settings(db, user.company_id, payload)


@router.get("/pricing", response_model=PricingSettingsOut)
def get_pricing_settings(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PricingSettingsOut:
    """The active pricing config (seeded with defaults on first read)."""
    row = pricing_service.ensure_active_config(db, user.company_id)
    return PricingSettingsOut(version=row.version, config=pricing_service.load_config(row))


@router.put("/pricing", response_model=PricingSettingsOut)
def update_pricing_settings(
    payload: PricingSettingsIn,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> PricingSettingsOut:
    """Store the full config document as a new active version."""
    row = pricing_service.set_config(db, user.company_id, payload.config)
    return PricingSettingsOut(version=row.version, config=pricing_service.load_config(row))
