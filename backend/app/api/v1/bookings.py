"""Authenticated booking endpoints: dashboard lists and completion with actuals."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.dashboard import BookingOut
from app.schemas.jobs import CompleteBookingIn, JobOut
from app.services import dashboard
from app.services import jobs as job_service

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.get("", response_model=list[BookingOut])
def list_bookings(
    status: str | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BookingOut]:
    """All bookings for the company, soonest first; optional status filter."""
    return dashboard.list_bookings(db, user.company_id, status=status)


@router.post("/{booking_id}/complete", response_model=JobOut)
def complete_booking(
    booking_id: uuid.UUID,
    payload: CompleteBookingIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    """Record the actuals for a finished move; creates the training job row."""
    job = job_service.complete_booking(
        db, company_id=user.company_id, booking_id=booking_id, payload=payload
    )
    return JobOut.model_validate(job)
