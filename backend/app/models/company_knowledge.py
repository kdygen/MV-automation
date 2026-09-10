"""Company knowledge model — tenant-specific policy and FAQ answers.

Backs the agent's ``search_company_knowledge`` tool: short, curated answers to
questions the quote itself cannot answer ("do you provide a COI?", "can you move a
piano?", "what's the cancellation policy?").

Rows rather than a blob on ``companies.settings``, deliberately: ``settings`` is read
on every intake and every quote for ``quote_review_mode``, so a growing knowledge base
there would inflate an unrelated hot path. Rows also give per-entry activation and
timestamps, server-side filtering, collision-free concurrent edits, and a natural CRUD
surface for the dashboard editor later.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID


class CompanyKnowledge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One curated answer belonging to exactly one company."""

    __tablename__ = "company_knowledge"
    __table_args__ = (
        # One entry per title per tenant: keeps a future dashboard editor's upserts
        # sane and stops accidental duplicates of the same FAQ.
        UniqueConstraint("company_id", "title", name="uq_company_knowledge_company_id_title"),
        # The only read pattern the agent has: this tenant's active entries.
        Index("ix_company_knowledge_company_id_is_active", "company_id", "is_active"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Curated synonyms bridging what customers say to what the entry is called
    # ("COI" → "Certificate of Insurance"). This is what lets simple keyword search
    # work well enough that embeddings are not yet needed.
    keywords: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<CompanyKnowledge {self.category}/{self.title!r} active={self.is_active}>"
