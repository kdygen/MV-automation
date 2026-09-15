"""Turning authored knowledge into a retrievable chunk index.

Two rules govern every path here.

**Generation swap, never in-place edit.** Re-indexing writes a new generation of chunks
and only removes the old one once every embedding has succeeded, inside one transaction.
A company's policy index therefore never has a window where it is half-replaced, and an
embedding provider outage mid-way costs the *new* index, not the working one.

**Embeddings are optional to store, never optional to attempt.** A chunk whose vector
could not be produced is still written and still lexically retrievable, which is what
keeps knowledge answerable when the provider is down.

Manual entries and uploaded documents are indexed by the same swap, through
:class:`_Parent`. They differ only in what a row is made of — an entry contributes its
curated keywords, a document contributes section headings and page numbers — so the part
that has to be transactionally correct exists once and is exercised by both.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.vector import DEFAULT_EMBEDDING_MODEL
from app.knowledge.chunking import Chunk, chunk_text
from app.knowledge.extraction import ExtractedDocument, attribute_pages
from app.models import CompanyKnowledge, KnowledgeChunk, KnowledgeDocument
from app.providers.embeddings import EmbeddingError, EmbeddingProvider

logger = get_logger(__name__)


@dataclass(frozen=True)
class IndexResult:
    """What an indexing pass did, for the dashboard and for tests."""

    chunks_written: int
    embedded: int
    generation: int
    model: str
    embedding_failed: bool = False


@dataclass(frozen=True)
class _Parent:
    """The thing whose chunks a generation swap owns.

    Exactly one of ``entry_id``/``document_id`` is set, mirroring the CHECK constraint on
    the table. Holding both in one object is what lets the swap below be written once.
    """

    company_id: uuid.UUID
    entry_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None

    @property
    def criterion(self) -> ColumnElement[bool]:
        if self.entry_id is not None:
            return KnowledgeChunk.knowledge_entry_id == self.entry_id
        return KnowledgeChunk.document_id == self.document_id


def _next_generation(db: Session, parent: _Parent) -> int:
    statement = select(func.max(KnowledgeChunk.generation)).where(
        KnowledgeChunk.company_id == parent.company_id, parent.criterion
    )
    return (db.scalar(statement) or 0) + 1


def _embed(
    provider: EmbeddingProvider | None, contents: list[str]
) -> tuple[list[list[float]] | None, str, bool]:
    """Embed a batch, reporting failure rather than raising.

    A failed embedding downgrades the index to lexical-only for those chunks. That is a
    worse index, but a working one — and far better than losing the content entirely.
    """
    if provider is None or not contents:
        return None, DEFAULT_EMBEDDING_MODEL, False
    try:
        result = provider.embed(contents)
        if len(result.vectors) != len(contents):
            raise EmbeddingError("provider returned a different number of vectors")
        return result.vectors, result.model, False
    except EmbeddingError:
        logger.warning("Embedding failed; chunks will be lexically retrievable only")
        return None, getattr(provider, "model", DEFAULT_EMBEDDING_MODEL), True


def _swap_generation(
    db: Session,
    parent: _Parent,
    rows: list[dict[str, Any]],
    *,
    provider: EmbeddingProvider | None,
) -> IndexResult:
    """Write a new generation of chunks, then retire every older one.

    The delete runs only after every insert has been flushed, so until the final
    statement the previous generation is still whole and still answering questions.
    """
    generation = _next_generation(db, parent)
    vectors, model, failed = _embed(provider, [row["content"] for row in rows])

    for position, row in enumerate(rows):
        db.add(
            KnowledgeChunk(
                company_id=parent.company_id,
                knowledge_entry_id=parent.entry_id,
                document_id=parent.document_id,
                generation=generation,
                embedding=vectors[position] if vectors else None,
                embedding_model=model if vectors else None,
                **row,
            )
        )
    db.flush()

    db.execute(
        delete(KnowledgeChunk).where(
            parent.criterion, KnowledgeChunk.generation < generation
        )
    )
    db.commit()

    return IndexResult(
        chunks_written=len(rows),
        embedded=len(rows) if vectors else 0,
        generation=generation,
        model=model,
        embedding_failed=failed,
    )


def _set_active(db: Session, criterion: ColumnElement[bool], active: bool) -> int:
    """Mirror a parent's visibility onto its chunks. Retrieval reads only the chunk."""
    chunks = list(db.scalars(select(KnowledgeChunk).where(criterion)))
    for chunk in chunks:
        chunk.is_active = active
    db.commit()
    return len(chunks)


def _remove(db: Session, criterion: ColumnElement[bool]) -> int:
    """Drop every chunk under one parent.

    Explicit rather than left to the foreign key's ``ON DELETE CASCADE``. The constraint
    is declared and does the work on PostgreSQL, but SQLite only enforces foreign keys
    when a connection opts in — so relying on it alone would mean the index behaves
    differently in tests than in production, which is exactly where stale chunks would
    hide.
    """
    removed = len(db.scalars(select(KnowledgeChunk.id).where(criterion)).all())
    db.execute(delete(KnowledgeChunk).where(criterion))
    db.commit()
    return removed


# --------------------------------------------------------------- manual entries


def index_knowledge_entry(
    db: Session,
    entry: CompanyKnowledge,
    *,
    provider: EmbeddingProvider | None = None,
) -> IndexResult:
    """(Re)build the chunk index for one manual knowledge entry.

    The entry's curated ``keywords`` are folded into the chunk text. Those synonyms are
    the reason Step 3A's keyword search worked at all — "COI" against "Certificate of
    Insurance" — and carrying them into both the vector and the lexical index is the
    single cheapest retrieval win available here.
    """
    parts = [entry.title, entry.content]
    if entry.keywords:
        parts.append(f"Also known as: {entry.keywords}")
    chunks = chunk_text("\n\n".join(parts), title=entry.title)

    rows = [
        {
            "chunk_index": chunk.index,
            "content": chunk.content,
            "content_hash": chunk.content_hash,
            # The entry's *title* is what a person recognises and what the agent tool has
            # always shown. The category is metadata, not a heading; storing it here
            # would hand the model "access" where Step 3A showed "Stairs and elevators".
            "heading": entry.title,
            "token_count": chunk.token_count,
            "chunk_metadata": {"category": entry.category},
            # Chunks inherit their parent's visibility; a deactivated entry must not
            # become retrievable just because it was re-indexed.
            "is_active": entry.is_active,
        }
        for chunk in chunks
    ]
    return _swap_generation(
        db, _Parent(company_id=entry.company_id, entry_id=entry.id), rows, provider=provider
    )


def set_entry_chunks_active(db: Session, entry: CompanyKnowledge) -> int:
    return _set_active(
        db, KnowledgeChunk.knowledge_entry_id == entry.id, entry.is_active
    )


def remove_entry_chunks(db: Session, entry_id: uuid.UUID) -> int:
    return _remove(db, KnowledgeChunk.knowledge_entry_id == entry_id)


# ------------------------------------------------------------ uploaded documents


def document_chunks(extracted: ExtractedDocument, title: str) -> list[Chunk]:
    """Split an extracted document. Separated so callers can count before committing."""
    return chunk_text(extracted.text, title=title)


def index_document(
    db: Session,
    document: KnowledgeDocument,
    extracted: ExtractedDocument,
    *,
    provider: EmbeddingProvider | None = None,
    active: bool = True,
) -> IndexResult:
    """(Re)build the chunk index for one uploaded document.

    ``active`` is passed in rather than read from the document because the row is still
    ``processing`` at this point: the caller is the only thing that knows whether this
    document is on its way to ``ready`` or is a retry of one the owner had switched off.
    """
    chunks = document_chunks(extracted, document.title)
    spans = attribute_pages(
        extracted.text, [chunk.content for chunk in chunks], extracted.page_starts
    )

    rows = [
        {
            "chunk_index": chunk.index,
            "content": chunk.content,
            "content_hash": chunk.content_hash,
            # A document's own section heading is the most recognisable label available;
            # its title is the fallback so the agent never shows "Company policy" for a
            # file the owner gave a name to.
            "heading": (chunk.heading or document.title)[:300],
            "page_from": page_from,
            "page_to": page_to,
            "token_count": chunk.token_count,
            "chunk_metadata": {"category": "policy", "document_title": document.title},
            "is_active": active,
        }
        for chunk, (page_from, page_to) in zip(chunks, spans, strict=True)
    ]
    return _swap_generation(
        db,
        _Parent(company_id=document.company_id, document_id=document.id),
        rows,
        provider=provider,
    )


def set_document_chunks_active(db: Session, document: KnowledgeDocument, active: bool) -> int:
    return _set_active(db, KnowledgeChunk.document_id == document.id, active)


def remove_document_chunks(db: Session, document_id: uuid.UUID) -> int:
    return _remove(db, KnowledgeChunk.document_id == document_id)


# ------------------------------------------------------------------- whole company


def reindex_company(
    db: Session, company_id: uuid.UUID, *, provider: EmbeddingProvider | None = None
) -> IndexResult:
    """Index every manual entry a company has. Used for backfill and model changes."""
    entries = list(
        db.scalars(select(CompanyKnowledge).where(CompanyKnowledge.company_id == company_id))
    )
    written = embedded = 0
    model = DEFAULT_EMBEDDING_MODEL
    failed = False
    for entry in entries:
        result = index_knowledge_entry(db, entry, provider=provider)
        written += result.chunks_written
        embedded += result.embedded
        model = result.model
        failed = failed or result.embedding_failed

    logger.info(
        "Indexed %d manual entries for company %s: %d chunks, %d embedded",
        len(entries),
        company_id,
        written,
        embedded,
    )
    return IndexResult(
        chunks_written=written,
        embedded=embedded,
        generation=1,
        model=model,
        embedding_failed=failed,
    )


def index_stats(db: Session, company_id: uuid.UUID) -> dict[str, int]:
    """Coverage of the chunk index, for the dashboard and for evaluation gating."""

    def count(model: type[KnowledgeChunk] | type[KnowledgeDocument], *where: Any) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    tenant = KnowledgeChunk.company_id == company_id
    return {
        "chunks": count(KnowledgeChunk, tenant),
        "active_chunks": count(KnowledgeChunk, tenant, KnowledgeChunk.is_active.is_(True)),
        "embedded_chunks": count(
            KnowledgeChunk,
            tenant,
            KnowledgeChunk.is_active.is_(True),
            KnowledgeChunk.embedding.is_not(None),
        ),
        "documents": count(
            KnowledgeDocument,
            KnowledgeDocument.company_id == company_id,
            KnowledgeDocument.deleted_at.is_(None),
        ),
    }
