"""Conversation and Message models — persistence for the post-quote sales agent.

A :class:`Conversation` is the chat thread attached to **one quote**: the customer
opens their tokenized quote link, asks questions, and the agent answers. Scope is
resolved server-side from that token, so ``Conversation.company_id`` is the tenant
boundary the agent's tool executor reads from — never something a model supplies.

:class:`Message` rows are the append-only transcript, including the agent's tool calls.
Recording ``tool_args``/``tool_result`` verbatim gives two guarantees after the fact:
that no model-supplied argument ever carried a tenant id, and that every price the
agent said came from a tool result computed by the deterministic pricing engine.

**Single source of truth for derivable state.** The conversation stores only
``company_id`` and ``quote_id``; the lead is reached via
``Conversation → Quote → MovingRequest → Lead``, and a message's tenant via
``Message → Conversation.company_id``. Copies of that state could drift out of sync
with their source, so they are deliberately not stored. If real query performance ever
demands denormalization, adding a column is a simpler migration than repairing rows
that disagree.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID, JSONType


class ConversationStatus(enum.StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class MessageRole(enum.StrEnum):
    """The three speakers in a tool-use loop."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One chat thread, bound to exactly one quote.

    ``quote_id`` is unique: the thread *is* "this quote's conversation". That makes
    get-or-create correct without a transaction dance — two concurrent first messages
    cannot create duplicates because the database rejects the second insert.
    """

    __tablename__ = "conversations"

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("quotes.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(ConversationStatus, native_enum=False, length=20, validate_strings=True),
        default=ConversationStatus.ACTIVE,
        nullable=False,
    )
    # Populated by a later summarization pass (dashboard "what did they discuss?").
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Conversation {self.id} quote={self.quote_id} status={self.status.value}>"


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One turn in a conversation: customer text, agent text, or a tool call/result.

    Tenant ownership is *not* stored here — it is derived through
    ``conversation_id → Conversation.company_id``, so a message can never disagree
    with its parent about which company it belongs to. Tenant-filtered reads join
    through ``Conversation``.
    """

    __tablename__ = "messages"
    __table_args__ = (
        # The dominant read: load one thread in chronological order.
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )

    role: Mapped[MessageRole] = mapped_column(
        SAEnum(MessageRole, native_enum=False, length=20, validate_strings=True),
        nullable=False,
    )
    # Nullable: an assistant turn that only calls a tool carries no prose, and a tool
    # row's payload lives in ``tool_result``.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Tool-call audit trail (set on tool rows only) ---
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_args: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    tool_result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    # --- Usage metering (set on assistant rows only) ---
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Message {self.id} role={self.role.value}>"
