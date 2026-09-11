"""Authenticated availability endpoints: the owner's booking calendar.

Lets an owner block a date or override its capacity. The customer-facing read lives on
the public router instead, scoped by quote token and stripped of load figures.

Reads are open to all staff (dispatchers need the calendar); writes require
owner/admin, matching ``/settings`` and ``/knowledge``.
"""

from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.db.session import get_db
from app.models import UserRole
from app.schemas.availability import DateCapacityIn, OwnerDayOut
from app.services import availability as availability_service

router = APIRouter(prefix="/availability", tags=["availability"])

_admin_only = require_roles(UserRole.OWNER, UserRole.ADMIN)


@router.get("", response_model=list[OwnerDayOut])
def list_availability(
    start: date_type = Query(alias="from"),
    end: date_type = Query(alias="to"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[OwnerDayOut]:
    """The owner's calendar for a date window, with booked counts and capacity."""
    return availability_service.owner_calendar(db, user.company_id, start=start, end=end)


@router.put("/{day}", response_model=OwnerDayOut)
def set_date_capacity(
    day: date_type,
    payload: DateCapacityIn,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> OwnerDayOut:
    """Block a date or override its capacity (idempotent upsert)."""
    return availability_service.set_date_capacity(db, user.company_id, day, payload)


@router.delete("/{day}", status_code=status.HTTP_204_NO_CONTENT)
def clear_date_capacity(
    day: date_type,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> Response:
    """Remove an override so the date falls back to the company default."""
    availability_service.clear_date_capacity(db, user.company_id, day)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
