"""Schemas for the public post-quote chat endpoint.

The request is deliberately the narrowest possible surface: a single message string.
``extra="forbid"`` means a body carrying ``company_id``, ``quote_id``,
``conversation_id`` or any other identifier is **rejected**, not quietly ignored —
scope comes only from the quote token in the URL.

The response carries the assistant's reply and, optionally, one allowlisted
``ui_action`` naming an on-screen control to offer. ``tools_used`` and
``conversation_id`` remain deliberately absent: the first would disclose internal
architecture to anyone probing the endpoint, and the second is an internal identifier
the frontend does not need, since the token already identifies the quote and each
quote has exactly one conversation. Both are recorded server-side instead.

``ui_action`` is a *hint*, not a grant. It names a control the customer could already
reach from the quote page, and every state change behind it still runs through the
deterministic endpoint that control calls. A tampered or unexpected value simply fails
to match a known action and renders nothing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.actions import UiAction

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
    """What the customer receives: the reply, plus at most a navigation hint."""

    reply: str
    #: One of :class:`~app.agent.actions.UiAction`; ``"none"`` when nothing is offered.
    ui_action: str = UiAction.NONE.value
