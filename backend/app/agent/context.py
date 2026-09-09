"""AgentContext — the trusted, server-created scope for one conversation.

Holds **immutable identifiers only**, never ORM objects. That is a security decision,
not a style one: an ORM ``Quote`` is session-bound and traversable
(``quote.company.quotes`` reaches sibling rows, and a lazy load can silently fetch data
outside the intended scope), whereas a frozen tuple of UUIDs can only be used by
queries we wrote explicitly and filtered ourselves.

Every field originates from a ``Conversation`` row that was resolved from a quote's
public token. No value here ever comes from a request body, a tool argument, or a
model. ``frozen=True`` means a tool handler cannot reassign the tenant mid-turn.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Conversation


@dataclass(frozen=True)
class AgentContext:
    """Trusted scope for one post-quote conversation.

    :param company_id: the tenant boundary — every tool query filters on it.
    :param conversation_id: the thread whose transcript may be read.
    :param quote_id: the single quote this conversation is about.

    Deliberately excludes ``moving_request_id`` and ``lead_id``: both are derivable in
    one join from ``quote_id``, and a cached copy could drift from its source.
    """

    company_id: uuid.UUID
    conversation_id: uuid.UUID
    quote_id: uuid.UUID

    @classmethod
    def from_conversation(cls, conversation: Conversation) -> AgentContext:
        """Build a context from a persisted conversation row (server-side only)."""
        return cls(
            company_id=conversation.company_id,
            conversation_id=conversation.id,
            quote_id=conversation.quote_id,
        )
