"""Uploaded policy documents and the chunk index both knowledge sources feed.

Two authoring surfaces, one retrieval index. A company's manually written
:class:`~app.models.company_knowledge.CompanyKnowledge` entries stay exactly as they
are — the owner edits them as rows, with categories and curated keywords — and uploaded
documents live here. Both project into :class:`KnowledgeChunk`, which is the only thing
retrieval ever reads.

Keeping them separate at the authoring layer and unified at the retrieval layer avoids
the two failure modes of the alternatives: two parallel retrievers that rank differently,
or folding manual entries into documents and losing the structured fields and CRUD the
dashboard is built on.

**Embeddings are an index, never the truth.** Extracted text, chunk content, ownership
and status all live in ordinary relational columns; the vector is a derived lookup aid
that can be rebuilt from them at any time.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID, JSONType
from app.db.vector import EmbeddingVector


class DocumentStatus(enum.StrEnum):
    PENDING = "pending"  # accepted, not yet parsed
    PROCESSING = "processing"  # parsing/chunking/embedding in flight
    READY = "ready"  # indexed and retrievable
    FAILED = "failed"  # see failure_reason; retryable
    INACTIVE = "inactive"  # retired by the owner, kept for the record


class KnowledgeDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One uploaded company policy document."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        # Re-uploading the same file must not double a company's policy index. Hashing
        # the *extracted text* rather than the bytes means a re-exported PDF with new
        # metadata is still recognised as the same document.
        UniqueConstraint("company_id", "content_hash", name="uq_knowledge_documents_content"),
        Index("ix_knowledge_documents_company_id_status", "company_id", "status"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, native_enum=False, length=20, validate_strings=True),
        default=DocumentStatus.PENDING,
        nullable=False,
    )
    #: Customer-safe explanation shown to the owner, e.g. "looks like a scanned document".
    failure_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Retained so re-chunking — after a chunking change or a model upgrade — never has
    #: to re-parse the original file, which may no longer exist.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Soft delete. A retired policy may still matter in a dispute, so the row survives;
    #: retrieval excludes it either way.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<KnowledgeDocument {self.title!r} {self.status.value}>"


class KnowledgeChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One retrievable passage, from a document or a manual entry.

    ``company_id`` and ``is_active`` are denormalized onto every chunk on purpose: the
    retrieval query must filter tenant and status in one indexed predicate **before** any
    similarity is computed. A better match in another company is never a candidate that
    loses — it is never a candidate.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        # Exactly one parent. A chunk that belonged to both, or neither, would have no
        # defined lifecycle when its owner is retired.
        CheckConstraint(
            "(document_id IS NULL) <> (knowledge_entry_id IS NULL)",
            name="ck_knowledge_chunks_single_parent",
        ),
        Index("ix_knowledge_chunks_company_id_is_active", "company_id", "is_active"),
        Index("ix_knowledge_chunks_document_id", "document_id"),
        Index("ix_knowledge_chunks_knowledge_entry_id", "knowledge_entry_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=True
    )
    knowledge_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("company_knowledge.id", ondelete="CASCADE"), nullable=True
    )

    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: What is embedded, lexically indexed, and shown. For a document chunk this is
    #: prefixed with its section heading so the section survives into the vector.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Kept separately so the tool's ``title`` can name the section without re-parsing.
    heading: Mapped[str | None] = mapped_column(String(300), nullable=True)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: NULL until embedded. A chunk with no vector is still lexically retrievable, which
    #: is what keeps the system usable when the embedding provider is down.
    embedding: Mapped[Any | None] = mapped_column(EmbeddingVector, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Bumped when a parent is re-indexed. The previous generation keeps serving until
    #: the replacement is complete, then is swapped out in one transaction.
    generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Display/debug metadata only — never consulted by ranking.
    chunk_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<KnowledgeChunk {self.chunk_index} {self.content[:40]!r}>"
