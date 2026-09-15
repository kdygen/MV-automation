"""Schemas for uploaded knowledge documents (Step 7E/7F).

What is *not* here is the point of the file. No ``company_id`` in either direction, no
``created_by_user_id``, no chunk ids, no embedding vectors, no ``source_hash`` — the
dashboard never needs an internal identifier to do its job, so none is published.

The one field worth arguing about is ``extracted_text``. It is returned, truncated, on
the detail endpoint only, because an owner whose upload produced strange answers has no
other way to find out that their PDF's two-column layout interleaved the paragraphs.
Hiding it would make every parsing complaint unanswerable.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.knowledge_document import DocumentStatus

#: How much extracted text the detail endpoint returns. A long policy is worth tens of
#: thousands of characters; past that the owner is scrolling, not checking.
MAX_EXTRACTED_TEXT_CHARS = 40_000


class KnowledgeDocumentOut(BaseModel):
    """One document as the dashboard list shows it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    original_filename: str
    mime_type: str
    byte_size: int
    status: DocumentStatus
    #: Owner-facing sentence naming the problem and the fix. ``None`` unless failed.
    failure_reason: str | None
    page_count: int | None
    #: Retrievable passages this document contributes.
    chunk_count: int
    #: How many of those carry a vector. Fewer than ``chunk_count`` means the document is
    #: searchable by keyword but not semantically — worth telling the owner, not an error.
    embedded_chunk_count: int
    indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentPassageOut(BaseModel):
    """One indexed passage, exactly as retrieval would see it."""

    model_config = ConfigDict(from_attributes=True)

    chunk_index: int
    heading: str | None
    page_from: int | None
    page_to: int | None
    token_count: int
    content: str
    is_embedded: bool


class KnowledgeDocumentDetail(KnowledgeDocumentOut):
    """A document plus what was actually extracted and indexed from it."""

    extracted_text: str | None
    extracted_text_truncated: bool
    passages: list[DocumentPassageOut]


class KnowledgeDocumentPatch(BaseModel):
    """The only in-place change a document supports: switching it on or off.

    Deliberately no ``title``. A document's title is part of every chunk's text, so a
    rename that did not re-embed would leave the index describing the document by a name
    the dashboard no longer shows — and one that did re-embed would make a text field
    silently spend money. Neither is worth it for a feature nobody asked for.
    """

    model_config = ConfigDict(extra="forbid")

    is_active: bool
