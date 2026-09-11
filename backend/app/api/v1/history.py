"""Authenticated historical-move endpoints: import, hand entry, and the history views.

Reads are open to all staff; writes require owner/admin, matching ``/settings``,
``/knowledge`` and ``/availability``. Historical data is what future pricing calibration
will rest on, so changing it deserves the same bar as changing prices.

``company_id`` is taken from the verified bearer token on every call. No route accepts a
company in its path, query, or body — and the canonical field registry has no
``company_id`` field, so a CSV column of that name is simply an unmapped column.

The upload endpoints take the file plus a JSON ``request`` form field, because a mapping
is structured data and multipart cannot nest. Preview and confirm both re-read the file;
confirm never trusts what a preview concluded.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.errors import ValidationError
from app.db.session import get_db
from app.models import UserRole
from app.schemas.history import (
    ConfirmOut,
    HistoricalMoveIn,
    HistoricalMovePatch,
    HistoricalSignalsOut,
    HistoryDetailOut,
    HistoryRowOut,
    HistorySummaryOut,
    ImportBatchOut,
    ImportRequest,
    InspectOut,
    PreviewOut,
    SimilarQueryIn,
)
from app.services import history as history_service
from app.services import insights as insights_service

router = APIRouter(prefix="/history", tags=["history"])

_admin_only = require_roles(UserRole.OWNER, UserRole.ADMIN)

#: A year of moves is a few hundred KB. 8 MB is generous for a decade of history and
#: bounds what one request can make the server buffer, parse, and hash.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


async def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError("File is too large (max 8 MB)")
    if not content:
        raise ValidationError("The uploaded file is empty")
    return file.filename or "upload.csv", content


def _parse_request(raw: str) -> ImportRequest:
    """Decode the mapping form field, reporting bad input as a client error."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("The column mapping is not valid JSON") from exc
    try:
        return ImportRequest.model_validate(payload)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first.get("loc", ()))
        message = f"{location}: {first.get('msg')}" if location else str(first)
        raise ValidationError(message) from exc


# --------------------------------------------------------------------------- views


@router.get("", response_model=list[HistoryRowOut])
def list_history(
    source: str | None = Query(default=None, description="platform | import"),
    limit: int = Query(default=200, ge=1, le=1000),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[HistoryRowOut]:
    """Completed moves for this company, newest first. Street addresses excluded."""
    return history_service.list_moves(db, user.company_id, source=source, limit=limit)


@router.get("/summary", response_model=HistorySummaryOut)
def history_summary(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HistorySummaryOut:
    """Counts and medians — the first check that an import landed correctly."""
    return history_service.summary(db, user.company_id)


@router.get("/imports", response_model=list[ImportBatchOut])
def list_imports(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ImportBatchOut]:
    return history_service.list_batches(db, user.company_id)


# --------------------------------------------------------------------------- import


@router.post("/import/inspect", response_model=InspectOut)
async def inspect_import(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(_admin_only),
) -> InspectOut:
    """Read an upload and propose a column mapping. **Writes nothing.**

    Needs no database at all, which is the clearest possible statement that looking at a
    file cannot change anything.
    """
    filename, content = await _read_upload(file)
    return history_service.inspect_upload(filename, content)


@router.post("/import/preview", response_model=PreviewOut)
async def preview_import(
    file: UploadFile = File(...),
    request: str = Form(..., description="JSON: mapping plus any parse choices"),
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> PreviewOut:
    """Judge every row against this company's existing history. **Writes nothing.**"""
    filename, content = await _read_upload(file)
    return history_service.preview_import(
        db, user.company_id, filename, content, _parse_request(request)
    )


@router.post("/import/confirm", response_model=ConfirmOut, status_code=status.HTTP_201_CREATED)
async def confirm_import(
    file: UploadFile = File(...),
    request: str = Form(...),
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> ConfirmOut:
    """Import the importable rows as one revertible batch."""
    filename, content = await _read_upload(file)
    return history_service.confirm_import(
        db, user.company_id, filename, content, _parse_request(request), user_id=user.id
    )


@router.post("/imports/{batch_id}/revert", response_model=ImportBatchOut)
def revert_import(
    batch_id: uuid.UUID,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> ImportBatchOut:
    """Remove the rows one import created, for when a mapping turns out to be wrong."""
    return history_service.revert_batch(db, user.company_id, batch_id)


# --------------------------------------------------------------------------- one move


@router.post("", response_model=HistoryDetailOut, status_code=status.HTTP_201_CREATED)
def create_move(
    payload: HistoricalMoveIn,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> HistoryDetailOut:
    """Add one historical move by hand, validated by the importer's own rules."""
    return history_service.create_move(db, user.company_id, payload)


@router.post("/similar", response_model=HistoricalSignalsOut)
def similar_moves(
    query: SimilarQueryIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HistoricalSignalsOut:
    """Comparable historical moves and what they did. **Evidence, not a price.**

    Returns durations, spread, and estimate bias drawn from this company's own history.
    It deliberately returns no recommended price and no aggregate money figure: pricing
    stays with the deterministic engine until a separately tested calibration layer
    exists to use signals like these.
    """
    return insights_service.similar_moves(db, user.company_id, query)


@router.get("/{move_id}/similar", response_model=HistoricalSignalsOut)
def similar_to_move(
    move_id: uuid.UUID,
    limit: int = Query(default=10, ge=1, le=50),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HistoricalSignalsOut:
    """Comparables for a move already in history — for sanity-checking imported data."""
    return insights_service.similar_to_move(db, user.company_id, move_id, limit=limit)


@router.get("/{move_id}", response_model=HistoryDetailOut)
def get_move(
    move_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HistoryDetailOut:
    return history_service.get_move(db, user.company_id, move_id)


@router.patch("/{move_id}", response_model=HistoryDetailOut)
def update_move(
    move_id: uuid.UUID,
    payload: HistoricalMovePatch,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> HistoryDetailOut:
    return history_service.update_move(db, user.company_id, move_id, payload)


@router.delete("/{move_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_move(
    move_id: uuid.UUID,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> Response:
    history_service.delete_move(db, user.company_id, move_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
