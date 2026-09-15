"""Turning authored knowledge into a retrievable chunk index.

Two rules govern every path here.

**Generation swap, never in-place edit.** Re-indexing writes a new generation of chunks
and only removes the old one once every embedding has succeeded, inside one transaction.
A company's policy index therefore never has a window where it is half-replaced, and an
embedding provider outage mid-way costs the *new* index, not the working one.

**Embeddings are optional to store, never optional to attempt.** A chunk whose vector
could not be produced is still written and still lexically retrievable, which is what
keeps knowledge answerable when the provider is down.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.vector import DEFAULT_EMBEDDING_MODEL
from app.knowledge.chunking import Chunk, chunk_text
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


def _next_generation(db: Session, *, company_id: uuid.UUID, entry_id: uuid.UUID | None,
                     document_id: uuid.UUID | None) -> int:
    statement = select(func.max(KnowledgeChunk.generation)).where(
        KnowledgeChunk.company_id == company_id
    )
    statement = (
        statement.where(KnowledgeChunk.knowledge_entry_id == entry_id)
        if entry_id is not None
        else statement.where(KnowledgeChunk.document_id == document_id)
    )
    return (db.scalar(statement) or 0) + 1


def _embed(
    provider: EmbeddingProvider | None, chunks: list[Chunk]
) -> tuple[list[list[float]] | None, str, bool]:
    """Embed a batch, reporting failure rather than raising.

    A failed embedding downgrades the index to lexical-only for those chunks. That is a
    worse index, but a working one — and far better than losing the content entirely.
    """
    if provider is None or not chunks:
        return None, DEFAULT_EMBEDDING_MODEL, False
    try:
        result = provider.embed([chunk.content for chunk in chunks])
        if len(result.vectors) != len(chunks):
            raise EmbeddingError("provider returned a different number of vectors")
        return result.vectors, result.model, False
    except EmbeddingError:
        logger.warning("Embedding failed; chunks will be lexically retrievable only")
        return None, getattr(provider, "model", DEFAULT_EMBEDDING_MODEL), True


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

    generation = _next_generation(
        db, company_id=entry.company_id, entry_id=entry.id, document_id=None
    )
    vectors, model, failed = _embed(provider, chunks)

    for position, chunk in enumerate(chunks):
        db.add(
            KnowledgeChunk(
                company_id=entry.company_id,
                knowledge_entry_id=entry.id,
                document_id=None,
                chunk_index=chunk.index,
                content=chunk.content,
                content_hash=chunk.content_hash,
                # The entry's *title* is what a person recognises and what the agent
                # tool has always shown. The category is metadata, not a heading; storing
                # it here would hand the model "access" where Step 3A showed
                # "Stairs and elevators".
                heading=entry.title,
                token_count=chunk.token_count,
                chunk_metadata={"category": entry.category},
                embedding=vectors[position] if vectors else None,
                embedding_model=model if vectors else None,
                generation=generation,
                # Chunks inherit their parent's visibility; a deactivated entry must not
                # become retrievable just because it was re-indexed.
                is_active=entry.is_active,
            )
        )
    db.flush()

    # Only now is the previous generation removed: until this statement the old index
    # was still whole and still serving.
    db.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.knowledge_entry_id == entry.id,
            KnowledgeChunk.generation < generation,
        )
    )
    db.commit()

    return IndexResult(
        chunks_written=len(chunks),
        embedded=len(chunks) if vectors else 0,
        generation=generation,
        model=model,
        embedding_failed=failed,
    )


def set_entry_chunks_active(db: Session, entry: CompanyKnowledge) -> int:
    """Mirror an entry's activation onto its chunks. Retrieval reads only the chunk."""
    chunks = list(
        db.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.knowledge_entry_id == entry.id)
        )
    )
    for chunk in chunks:
        chunk.is_active = entry.is_active
    db.commit()
    return len(chunks)


def remove_entry_chunks(db: Session, entry_id: uuid.UUID) -> int:
    """Drop every chunk belonging to one entry.

    Explicit rather than left to the foreign key's ``ON DELETE CASCADE``. The constraint
    is declared and does the work on PostgreSQL, but SQLite only enforces foreign keys
    when a connection opts in — so relying on it alone would mean the index behaves
    differently in tests than in production, which is exactly where stale chunks would
    hide.
    """
    removed = len(
        db.scalars(
            select(KnowledgeChunk.id).where(KnowledgeChunk.knowledge_entry_id == entry_id)
        ).all()
    )
    db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.knowledge_entry_id == entry_id))
    db.commit()
    return removed


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
    total = db.scalar(
        select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.company_id == company_id
        )
    )
    active = db.scalar(
        select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.company_id == company_id, KnowledgeChunk.is_active.is_(True)
        )
    )
    embedded = db.scalar(
        select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.company_id == company_id,
            KnowledgeChunk.is_active.is_(True),
            KnowledgeChunk.embedding.is_not(None),
        )
    )
    documents = db.scalar(
        select(func.count()).select_from(KnowledgeDocument).where(
            KnowledgeDocument.company_id == company_id
        )
    )
    return {
        "chunks": total or 0,
        "active_chunks": active or 0,
        "embedded_chunks": embedded or 0,
        "documents": documents or 0,
    }
