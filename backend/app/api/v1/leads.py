"""Authenticated lead endpoints (dashboard lists and detail)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.dashboard import LeadDetailOut, LeadOut
from app.services import dashboard

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("", response_model=list[LeadOut])
def list_leads(
    status: str | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LeadOut]:
    """All leads for the company, newest first; optional status filter."""
    return dashboard.list_leads(db, user.company_id, status=status)


@router.get("/{lead_id}", response_model=LeadDetailOut)
def get_lead(
    lead_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadDetailOut:
    """Lead detail with its moving requests and quotes."""
    return dashboard.get_lead_detail(db, user.company_id, lead_id)
