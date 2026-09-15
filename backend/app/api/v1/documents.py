"""Uploaded knowledge documents: the dashboard's ingestion endpoints.

Reads are open to all staff, writes require owner/admin — the same bar as manual
knowledge entries, for the same reason: a document uploaded here becomes statements the
sales agent repeats to customers as company policy.

**Ingestion runs after the response.** Parsing a 40-page PDF and embedding a hundred
passages takes seconds to tens of seconds, which is far too long to hold an upload
request open. The route persists the file as ``pending`` and hands the bytes to a
background task, so the dashboard gets an immediate row to poll and the owner watches
``pending → processing → ready`` instead of a spinner that might time out.

Tenancy: ``user.company_id`` comes from the verified bearer token and is part of every
lookup. No route accepts a company identifier anywhere, and the background task is given
the tenant explicitly rather than re-deriving it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    SessionScope,
    embedding_provider_dep,
    get_current_user,
    require_roles,
    session_scope_dep,
)
from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.db.session import get_db
from app.models import UserRole
from app.providers.embeddings import EmbeddingProvider
from app.schemas.documents import (
    KnowledgeDocumentDetail,
    KnowledgeDocumentOut,
    KnowledgeDocumentPatch,
)
from app.services import documents as documents_service

logger = get_logger(__name__)

# Mounted ahead of the knowledge router so "documents" is matched as a literal segment
# and never offered to the ``/knowledge/{entry_id}`` route as a candidate id.
router = APIRouter(prefix="/knowledge/documents", tags=["knowledge"])

_admin_only = require_roles(UserRole.OWNER, UserRole.ADMIN)

#: Read in slices so an oversized upload is rejected after one megabyte rather than
#: after the client has finished sending twenty. ``UploadFile.read()`` with no argument
#: would buffer the whole body first, which is exactly what the limit exists to prevent.
_READ_CHUNK = 1024 * 1024


async def _read_capped(file: UploadFile, limit: int) -> bytes:
    parts: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > limit:
            raise ValidationError(
                f"That file is larger than {limit // (1024 * 1024)} MB. "
                "Upload a smaller file, or split it into sections."
            )
        parts.append(chunk)
    if not total:
        raise ValidationError("That file is empty.")
    return b"".join(parts)


def _ingest(
    scope: SessionScope,
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    data: bytes | None,
    provider: EmbeddingProvider | None,
) -> None:
    """Run ingestion on its own session, after the response has been sent.

    Nothing is raised out of here. The service writes a status for every outcome, and an
    exception escaping a background task would leave the owner watching a row that never
    moves, with no reason recorded anywhere they can see.
    """
    try:
        with scope() as session:
            if data is None:
                documents_service.reprocess_document(
                    session, company_id, document_id, provider=provider
                )
            else:
                documents_service.process_upload(
                    session, company_id, document_id, data, provider=provider
                )
    except Exception:  # noqa: BLE001 - last line of defence for a detached task
        logger.exception("Ingestion task failed for document %s", document_id)


@router.get("", response_model=list[KnowledgeDocumentOut])
def list_documents(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KnowledgeDocumentOut]:
    """Every document this company still has, newest first."""
    return documents_service.list_documents(db, user.company_id)


@router.post("", response_model=KnowledgeDocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: EmbeddingProvider | None = Depends(embedding_provider_dep),
    scope: SessionScope = Depends(session_scope_dep),
) -> KnowledgeDocumentOut:
    """Accept a policy document and queue it for indexing."""
    data = await _read_capped(file, settings.knowledge_max_upload_bytes)
    document = documents_service.create_upload(
        db,
        user.company_id,
        user.id,
        filename=file.filename or "document",
        data=data,
        title=(title or "").strip() or None,
        max_documents=settings.knowledge_max_documents_per_company,
    )
    background.add_task(_ingest, scope, user.company_id, document.id, data, provider)
    return documents_service.get_document(db, user.company_id, document.id)


@router.get("/{document_id}", response_model=KnowledgeDocumentDetail)
def get_document(
    document_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgeDocumentDetail:
    """One document, its extracted text, and the passages built from it."""
    return documents_service.get_document(db, user.company_id, document_id)


@router.post("/{document_id}/retry", response_model=KnowledgeDocumentDetail)
def retry_document(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
    provider: EmbeddingProvider | None = Depends(embedding_provider_dep),
    scope: SessionScope = Depends(session_scope_dep),
) -> KnowledgeDocumentDetail:
    """Re-chunk and re-embed from the stored text.

    The eligibility checks run here, synchronously, so an owner who clicks retry on a
    document that can never succeed is told why immediately instead of watching it fail
    a second time.
    """
    documents_service.assert_retryable(db, user.company_id, document_id)
    background.add_task(_ingest, scope, user.company_id, document_id, None, provider)
    return documents_service.get_document(db, user.company_id, document_id)


@router.patch("/{document_id}", response_model=KnowledgeDocumentOut)
def patch_document(
    document_id: uuid.UUID,
    payload: KnowledgeDocumentPatch,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> KnowledgeDocumentOut:
    """Switch an indexed document on or off for retrieval."""
    return documents_service.set_active(
        db, user.company_id, document_id, active=payload.is_active
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> Response:
    """Retire a document. Soft delete: out of the dashboard and out of retrieval."""
    documents_service.soft_delete(db, user.company_id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
