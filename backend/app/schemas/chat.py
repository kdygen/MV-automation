"""Schemas for the public post-quote chat endpoint.

The request is deliberately the narrowest possible surface: a single message string.
``extra="forbid"`` means a body carrying ``company_id``, ``quote_id``,
``conversation_id`` or any other identifier is **rejected**, not quietly ignored —
scope comes only from the quote token in the URL.

The response carries only the assistant's reply. ``tools_used`` and
``conversation_id`` are deliberately absent: the first would disclose internal
architecture to anyone probing the endpoint, and the second is an internal identifier
the frontend does not need, since the token already identifies the quote and each
quote has exactly one conversation. Both are recorded server-side instead.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Roughly 400 words — ample for a question about a move, while bounding per-call
#: token cost and abuse. Over-long messages are rejected, never truncated: answering
#: half a question silently is worse than telling the customer it was too long.
MAX_MESSAGE_LENGTH = 2000


class ChatMessageIn(BaseModel):
    """One customer message. The only customer-controlled input besides the token."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class ChatReplyOut(BaseModel):
    """What the customer receives: the reply, and nothing else."""

    reply: str
