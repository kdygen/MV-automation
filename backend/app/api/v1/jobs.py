"""Authenticated job endpoints: history list, CSV import, accuracy summary."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.errors import ValidationError
from app.db.session import get_db
from app.models import UserRole
from app.schemas.jobs import AccuracySummaryOut, ImportResultOut, JobOut
from app.services import jobs as job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])

MAX_IMPORT_BYTES = 2 * 1024 * 1024  # 2 MB ≈ tens of thousands of rows


@router.get("", response_model=list[JobOut])
def list_jobs(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    """Completed jobs (platform + imported), newest move first."""
    return job_service.list_jobs(db, user.company_id)


@router.get("/accuracy", response_model=AccuracySummaryOut)
def accuracy(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccuracySummaryOut:
    """Quote-vs-actual accuracy stats for this company."""
    return job_service.accuracy_summary(db, user.company_id)


@router.post("/import", response_model=ImportResultOut)
async def import_jobs(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> ImportResultOut:
    """Import historical jobs from a CSV file (solves the ML cold-start problem)."""
    content = await file.read()
    if len(content) > MAX_IMPORT_BYTES:
        raise ValidationError("CSV file is too large (max 2 MB)")
    return job_service.import_jobs_csv(db, user.company_id, content)
