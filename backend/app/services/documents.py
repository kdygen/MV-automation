"""Uploaded knowledge documents: ingestion, lifecycle, and the dashboard's view of them.

Ingestion is a state machine, not a function call, because every step can fail for a
reason the owner needs to read:

    pending ──▶ processing ──▶ ready ⇄ inactive
                    │
                    └────────▶ failed ──(retry)──▶ processing

**Nothing is thrown away on failure.** A document that could not be parsed still exists
as a row carrying the reason, because the alternative — rejecting the upload outright —
leaves the owner with a file they believe they uploaded and no trace of why it is not
answering questions.

**Extracted text is stored; the original file is not.** Re-indexing after a chunking or
model change therefore needs no file store, and we hold only the text we actually use.
The cost is that a retry re-chunks from that text rather than re-parsing, which is why
page numbers do not survive a retry — see :func:`reprocess_document`.

Tenancy: every function takes ``company_id`` from the caller's verified identity and
makes it part of the lookup, never a check afterwards. A document belonging to another
company is indistinguishable from one that does not exist.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.knowledge.extraction import (
    MIME_TYPES,
    DocumentKind,
    ExtractedDocument,
    ExtractionError,
    detect_kind,
    extract,
    file_extension,
)
from app.models import DocumentStatus, KnowledgeChunk, KnowledgeDocument
from app.providers.embeddings import EmbeddingProvider
from app.schemas.documents import (
    MAX_EXTRACTED_TEXT_CHARS,
    DocumentPassageOut,
    KnowledgeDocumentDetail,
    KnowledgeDocumentOut,
)
from app.services import indexing

logger = get_logger(__name__)

MAX_TITLE_LENGTH = 300

#: Statuses a retry is always meaningful from. ``processing`` is included on purpose: a
#: process that died mid-ingest leaves a row stuck there, and retry is the only way an
#: owner can get out of it unaided.
#:
#: A ``ready`` document is normally finished — but not if it was indexed while the
#: embedding provider was down, which leaves it keyword-searchable with no vectors and no
#: way to fix itself. :func:`assert_retryable` treats an incomplete index as retryable
#: whatever the status says, because "ready" describes the document, not the index.
RETRYABLE = frozenset({DocumentStatus.FAILED, DocumentStatus.PROCESSING, DocumentStatus.PENDING})

_FILENAME_NOISE = re.compile(r"[_\-]+")


class UploadRejected(ValidationError):
    """The file cannot become a document at all, so no row is created."""


def _now() -> datetime:
    return datetime.now(UTC)


def source_hash(data: bytes) -> str:
    """Identity of the uploaded bytes.

    Domain-separated from :func:`content_hash` so a byte digest and a text digest can
    never be mistaken for one another if the two columns are ever compared.
    """
    return hashlib.sha256(b"source:" + data).hexdigest()


def content_hash(text: str) -> str:
    """Identity of the extracted text — what decides whether two files say the same thing."""
    return hashlib.sha256(b"text:" + text.encode()).hexdigest()


def title_from_filename(filename: str) -> str:
    """"2025_cancellation-policy.pdf" → "2025 cancellation policy"."""
    stem = filename[: -len(ext)] if (ext := file_extension(filename)) else filename
    cleaned = _FILENAME_NOISE.sub(" ", stem).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:MAX_TITLE_LENGTH] or "Untitled document")


# --------------------------------------------------------------------------- reads


@dataclass(frozen=True)
class _Counts:
    chunks: int
    embedded: int


def _chunk_counts(db: Session, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, _Counts]:
    """Chunk and embedding counts per document, in one query rather than N."""
    if not document_ids:
        return {}
    rows = db.execute(
        select(
            KnowledgeChunk.document_id,
            func.count(KnowledgeChunk.id),
            func.count(KnowledgeChunk.embedding),
        )
        .where(KnowledgeChunk.document_id.in_(document_ids))
        .group_by(KnowledgeChunk.document_id)
    ).all()
    return {
        document_id: _Counts(chunks=int(total), embedded=int(embedded))
        for document_id, total, embedded in rows
        if document_id is not None
    }


def _to_out(document: KnowledgeDocument, counts: _Counts) -> KnowledgeDocumentOut:
    return KnowledgeDocumentOut(
        id=document.id,
        title=document.title,
        original_filename=document.original_filename,
        mime_type=document.mime_type,
        byte_size=document.byte_size,
        status=document.status,
        failure_reason=document.failure_reason,
        page_count=document.page_count,
        chunk_count=counts.chunks,
        embedded_chunk_count=counts.embedded,
        indexed_at=document.indexed_at,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def list_documents(db: Session, company_id: uuid.UUID) -> list[KnowledgeDocumentOut]:
    """Every document this company still has, newest first.

    Soft-deleted rows are excluded. They are retained for the record, but showing them
    here would make "delete" look like it had not worked, and there is no restore action
    in the dashboard for them to be the subject of — re-uploading the file revives the
    row, which is the same thing arrived at more obviously.
    """
    documents = list(
        db.scalars(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.company_id == company_id,
                KnowledgeDocument.deleted_at.is_(None),
            )
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id)
        )
    )
    counts = _chunk_counts(db, [d.id for d in documents])
    return [_to_out(d, counts.get(d.id, _Counts(0, 0))) for d in documents]


def _get_owned(
    db: Session, company_id: uuid.UUID, document_id: uuid.UUID
) -> KnowledgeDocument:
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.company_id == company_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
    )
    if document is None:
        raise NotFoundError("Document not found")
    return document


def get_document(
    db: Session, company_id: uuid.UUID, document_id: uuid.UUID
) -> KnowledgeDocumentDetail:
    """One document with its extracted text and the passages built from it."""
    document = _get_owned(db, company_id, document_id)
    chunks = list(
        db.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document.id)
            .order_by(KnowledgeChunk.chunk_index)
        )
    )
    text = document.extracted_text or ""
    counts = _Counts(
        chunks=len(chunks), embedded=sum(1 for c in chunks if c.embedding is not None)
    )
    return KnowledgeDocumentDetail(
        **_to_out(document, counts).model_dump(),
        extracted_text=text[:MAX_EXTRACTED_TEXT_CHARS] or None,
        extracted_text_truncated=len(text) > MAX_EXTRACTED_TEXT_CHARS,
        passages=[
            DocumentPassageOut(
                chunk_index=chunk.chunk_index,
                heading=chunk.heading,
                page_from=chunk.page_from,
                page_to=chunk.page_to,
                token_count=chunk.token_count,
                content=chunk.content,
                is_embedded=chunk.embedding is not None,
            )
            for chunk in chunks
        ],
    )


# -------------------------------------------------------------------------- upload


def create_upload(
    db: Session,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
    *,
    filename: str,
    data: bytes,
    title: str | None,
    max_documents: int,
) -> KnowledgeDocument:
    """Accept a file and record it as ``pending``. Parsing happens afterwards.

    Two checks run before anything is written, because both have answers the owner can
    act on immediately: the file type (sniffed from the bytes, never from the browser's
    content type) and the per-company document cap. Everything that can only be known by
    parsing is left to :func:`process_upload`, which reports it on the row.
    """
    try:
        kind = detect_kind(filename, data)
    except ExtractionError as exc:
        # Sniffing the bytes is the one check worth making before a row exists: the
        # answer ("this is not a PDF") is the same however many times it is retried.
        raise UploadRejected(exc.reason) from exc

    existing = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.company_id == company_id,
            KnowledgeDocument.source_hash == source_hash(data),
        )
    )
    if existing is not None:
        return _revive(db, existing, filename=filename, kind=kind, data=data, title=title)

    active = db.scalar(
        select(func.count())
        .select_from(KnowledgeDocument)
        .where(
            KnowledgeDocument.company_id == company_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
    )
    if (active or 0) >= max_documents:
        raise ConflictError(
            f"You have reached the limit of {max_documents} documents. "
            "Delete one you no longer need before uploading another."
        )

    document = KnowledgeDocument(
        company_id=company_id,
        title=(title or title_from_filename(filename))[:MAX_TITLE_LENGTH],
        original_filename=filename[:255],
        mime_type=MIME_TYPES[kind],
        byte_size=len(data),
        status=DocumentStatus.PENDING,
        source_hash=source_hash(data),
        content_hash=None,
        created_by_user_id=user_id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _revive(
    db: Session,
    document: KnowledgeDocument,
    *,
    filename: str,
    kind: DocumentKind,
    data: bytes,
    title: str | None,
) -> KnowledgeDocument:
    """Re-uploading a file we have seen before.

    A failed or deleted document is reset and processed again — that is what an owner
    re-uploading a file means, and refusing them would leave a dead row they cannot get
    past. So is one stuck at ``pending`` or ``processing`` with nothing extracted: that is
    an ingest that died before it stored any text, so retry has nothing to work from and
    re-uploading the file is the only route out. Between them those two cases mean no
    upload can ever become unrecoverable.

    A document that is live, switched off, or mid-ingest with text already stored is a
    genuine duplicate and is refused — silently re-indexing it would throw away the
    ``inactive`` decision the owner made on purpose, or duplicate work already in flight.
    """
    stuck = (
        document.status in {DocumentStatus.PENDING, DocumentStatus.PROCESSING}
        and not document.extracted_text
    )
    revivable = (
        document.status is DocumentStatus.FAILED or document.deleted_at is not None or stuck
    )
    if not revivable:
        raise ConflictError(
            f"You have already uploaded this file as {document.title!r}."
        )

    document.title = (title or document.title)[:MAX_TITLE_LENGTH]
    document.original_filename = filename[:255]
    document.mime_type = MIME_TYPES[kind]
    document.byte_size = len(data)
    document.status = DocumentStatus.PENDING
    document.failure_reason = None
    document.deleted_at = None
    document.content_hash = None
    document.indexed_at = None
    db.commit()
    db.refresh(document)
    return document


# ---------------------------------------------------------------------- ingestion


def process_upload(
    db: Session,
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    data: bytes,
    *,
    provider: EmbeddingProvider | None = None,
) -> DocumentStatus:
    """Parse, chunk, embed and index a freshly uploaded file."""
    return _ingest(db, company_id, document_id, data=data, provider=provider)


def assert_retryable(
    db: Session, company_id: uuid.UUID, document_id: uuid.UUID
) -> KnowledgeDocument:
    """Raise unless this document can usefully be re-indexed.

    Split out so the route can answer "why not?" while the request is still open, rather
    than letting a background task discover it and write a second failure the owner has
    to go looking for.
    """
    document = _get_owned(db, company_id, document_id)
    if document.status not in RETRYABLE and _fully_embedded(db, document.id):
        raise ConflictError("This document is already fully indexed.")
    if not document.extracted_text:
        raise ValidationError(
            "No readable text was stored for this document, so there is nothing to "
            "retry. Upload a text-based copy instead."
        )
    return document


def _fully_embedded(db: Session, document_id: uuid.UUID) -> bool:
    """Whether every passage of this document carries a vector."""
    counts = _chunk_counts(db, [document_id]).get(document_id)
    return counts is not None and counts.chunks > 0 and counts.chunks == counts.embedded


def reprocess_document(
    db: Session,
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    provider: EmbeddingProvider | None = None,
) -> DocumentStatus:
    """Re-chunk and re-embed from the stored text. The retry path.

    This is what recovers a document whose embeddings failed or whose ingestion died
    part-way: the text is already saved, so nothing has to be uploaded again. It cannot
    recover a document that never parsed — there is no text to work from, and the honest
    answer is to say so rather than loop.

    Page numbers are the one casualty. They are derived from where the extractor found
    each page in the original file, which is not something the stored text records, so a
    retried document's passages report no pages. A worse label on a working index beats
    keeping a 20 MB PDF in the database to preserve it.
    """
    assert_retryable(db, company_id, document_id)
    return _ingest(db, company_id, document_id, data=None, provider=provider)


def _ingest(
    db: Session,
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    data: bytes | None,
    provider: EmbeddingProvider | None,
) -> DocumentStatus:
    """The one path from ``processing`` to ``ready`` or ``failed``.

    Runs as a background task, so it must never raise: an exception escaping here would
    be logged and lost, and the owner would watch a document sit at ``processing``
    forever with no reason given. Every exit writes a status.
    """
    document = _get_owned(db, company_id, document_id)
    # Committed before any parsing so the dashboard shows movement, and so a crash leaves
    # a state that says "in flight" rather than "queued".
    was_inactive = document.status is DocumentStatus.INACTIVE
    document.status = DocumentStatus.PROCESSING
    document.failure_reason = None
    db.commit()

    try:
        extracted = _text_for(document, data)
    except ExtractionError as exc:
        return _fail(db, document, exc.reason)
    except Exception:
        logger.exception("Unexpected failure extracting document %s", document.id)
        return _fail(
            db, document, "This document could not be processed. Please try again."
        )

    duplicate = db.scalar(
        select(KnowledgeDocument.title).where(
            KnowledgeDocument.company_id == company_id,
            KnowledgeDocument.content_hash == content_hash(extracted.text),
            KnowledgeDocument.id != document.id,
        )
    )
    if duplicate is not None:
        return _fail(
            db,
            document,
            f"This document contains the same text as {duplicate!r}, which you have "
            "already uploaded.",
        )

    document.extracted_text = extracted.text
    document.page_count = extracted.page_count
    document.content_hash = content_hash(extracted.text)

    try:
        result = indexing.index_document(
            db, document, extracted, provider=provider, active=not was_inactive
        )
    except IntegrityError:
        # The duplicate pre-check above loses a race with a concurrent upload of the same
        # text; the unique constraint is what actually decides it.
        db.rollback()
        return _fail(
            db, document, "This document duplicates one you have already uploaded."
        )
    except Exception:
        logger.exception("Unexpected failure indexing document %s", document.id)
        db.rollback()
        return _fail(
            db, document, "This document could not be indexed. Please try again."
        )

    if result.chunks_written == 0:
        return _fail(db, document, "No readable text was found in this document.")

    document.status = DocumentStatus.INACTIVE if was_inactive else DocumentStatus.READY
    document.indexed_at = _now()
    document.embedding_model = result.model if result.embedded else None
    document.failure_reason = None
    db.commit()

    logger.info(
        "Indexed document %s: %d passages, %d embedded%s",
        document.id,
        result.chunks_written,
        result.embedded,
        " (embeddings unavailable)" if result.embedding_failed else "",
    )
    return document.status


def _text_for(document: KnowledgeDocument, data: bytes | None) -> ExtractedDocument:
    """Extract from the uploaded bytes, or rebuild from stored text on a retry.

    The rebuilt document carries no ``page_starts``, which is what costs a retry its page
    attribution — the offsets live in the original file, and we do not keep it.
    """
    if data is not None:
        return extract(document.original_filename, data)
    if not document.extracted_text:
        raise ExtractionError("No readable text was found in this document.")
    return ExtractedDocument(
        text=document.extracted_text,
        kind=_kind_of(document),
        page_count=document.page_count,
    )


def _kind_of(document: KnowledgeDocument) -> DocumentKind:
    """The kind a stored document was parsed as, recovered from its canonical MIME."""
    for kind, mime in MIME_TYPES.items():
        if mime == document.mime_type:
            return kind
    return DocumentKind.TEXT


def _fail(db: Session, document: KnowledgeDocument, reason: str) -> DocumentStatus:
    """Record a failure and take the document out of retrieval.

    Chunks from a previous successful generation are deactivated rather than deleted: the
    document is not answering questions any more, but if the owner retries and succeeds
    the rows are still there to be replaced by the generation swap.
    """
    document.status = DocumentStatus.FAILED
    document.failure_reason = reason[:300]
    db.commit()
    indexing.set_document_chunks_active(db, document, False)
    logger.info("Document %s failed: %s", document.id, reason)
    return DocumentStatus.FAILED


# ------------------------------------------------------------------- lifecycle


def set_active(
    db: Session, company_id: uuid.UUID, document_id: uuid.UUID, *, active: bool
) -> KnowledgeDocumentOut:
    """Switch an indexed document on or off for retrieval.

    Both the document's status and its chunks' ``is_active`` flag are updated. Retrieval
    already excludes chunks whose document is not ``ready``, so either one alone would be
    enough — which is exactly why both are set. A single forgotten filter in a future
    query should not be able to put a retired policy back in front of a customer.
    """
    document = _get_owned(db, company_id, document_id)
    allowed = DocumentStatus.INACTIVE if active else DocumentStatus.READY
    if document.status is not allowed:
        raise ConflictError(
            "Only a document that finished processing can be switched on or off."
        )

    document.status = DocumentStatus.READY if active else DocumentStatus.INACTIVE
    db.commit()
    indexing.set_document_chunks_active(db, document, active)

    counts = _chunk_counts(db, [document.id]).get(document.id, _Counts(0, 0))
    return _to_out(document, counts)


def soft_delete(db: Session, company_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """Retire a document: gone from the dashboard, gone from retrieval, kept on record.

    A policy the company has withdrawn can still matter in a dispute about a move quoted
    while it was live, so the row and its extracted text survive. What does not survive
    is any path to a customer: ``deleted_at`` alone already excludes every chunk through
    the retrieval predicate, and the chunks are deactivated too.
    """
    document = _get_owned(db, company_id, document_id)
    document.deleted_at = _now()
    document.status = DocumentStatus.INACTIVE
    db.commit()
    indexing.set_document_chunks_active(db, document, False)
    logger.info("Document %s soft-deleted", document.id)
