"""Schemas for the company knowledge dashboard (Step 3B).

The write schemas share one set of ``Annotated`` string types so trimming, blank
rejection, and length limits are declared once and reused by both create and patch.
``strip_whitespace`` runs before ``min_length``, so a whitespace-only field trims to the
empty string and fails validation — the "reject blank content" rule falls out of the
type rather than needing its own validator.

``company_id`` appears in none of these models, in either direction: it is derived from
the authenticated user and never travels over the wire.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

#: Length limits mirror the ``company_knowledge`` columns. ``content`` is a Text column
#: with no database limit, but agent answers should be a short paragraph — a cap here
#: keeps one entry from swallowing a whole model turn.
MAX_CATEGORY_LENGTH = 50
MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 4000
MAX_KEYWORDS_LENGTH = 500

Category = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CATEGORY_LENGTH)
]
Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH)
]
Content = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CONTENT_LENGTH)
]
Keywords = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=MAX_KEYWORDS_LENGTH)
]


class KnowledgeEntryOut(BaseModel):
    """One entry as the dashboard sees it.

    ``id`` is included only because update and delete route on it. ``created_at`` is
    not: the useful question an owner asks is "is this still current?", which
    ``updated_at`` answers and ``created_at`` does not.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str
    title: str
    content: str
    keywords: str | None
    is_active: bool
    updated_at: datetime


class KnowledgeEntryIn(BaseModel):
    """POST payload. Unknown keys are refused rather than ignored."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    title: Title
    content: Content
    keywords: Keywords | None = None
    is_active: bool = True


class KnowledgeEntryPatch(BaseModel):
    """PATCH payload — only supplied fields change.

    Every field is optional, but a supplied field must still be valid: sending
    ``{"content": "   "}`` is a validation error, not a way to blank an answer the
    agent will quote.
    """

    model_config = ConfigDict(extra="forbid")

    category: Category | None = None
    title: Title | None = None
    content: Content | None = None
    keywords: Keywords | None = None
    is_active: bool | None = None


class StarterTopicOut(BaseModel):
    """A suggested topic for onboarding — a question, never an answer.

    ``prompt`` is the customer question the owner is answering; ``title`` is what the
    entry will be called. No ``content``: the company supplies every business fact.
    """

    category: str = Field(max_length=MAX_CATEGORY_LENGTH)
    title: str
    prompt: str
    keywords: str
