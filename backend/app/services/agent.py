"""Agent service: context construction and tenant-safe transcript reads.

Services own database writes, so conversation creation lives here rather than in the
``app.agent`` package (which stays read-only and dependency-light).

This module is the **only** place that builds an :class:`AgentContext`. Everything
downstream — tools, and later the loop and endpoint — receives a context it cannot
influence.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.context import AgentContext
from app.core.errors import ForbiddenError
from app.core.logging import get_logger
from app.models import Conversation, Message, Quote
from app.services import quotes as quote_service

logger = get_logger(__name__)


def get_or_create_conversation(db: Session, quote: Quote) -> Conversation:
    """Return the conversation for ``quote``, creating it on first use.

    ``UNIQUE(quote_id)`` makes this safe under concurrency: if two first messages race,
    one insert wins and the loser re-reads the winner's row rather than creating a
    duplicate thread.
    """
    existing = db.scalar(select(Conversation).where(Conversation.quote_id == quote.id))
    if existing is not None:
        return existing

    conversation = Conversation(company_id=quote.company_id, quote_id=quote.id)
    db.add(conversation)
    try:
        db.commit()
    except IntegrityError:
        # Lost the race — the other writer's row is authoritative.
        db.rollback()
        winner = db.scalar(select(Conversation).where(Conversation.quote_id == quote.id))
        if winner is None:  # pragma: no cover - only if the row vanished mid-race
            raise
        return winner

    logger.info("Opened conversation %s for quote %s", conversation.id, quote.id)
    return conversation


def build_agent_context(db: Session, token: str) -> AgentContext:
    """Resolve a customer's quote token into a trusted agent scope.

    The whole chain is server-side: ``token → Quote → Conversation → AgentContext``.
    No caller-supplied identifier participates.

    :raises app.core.errors.NotFoundError: the token matches no quote.
    :raises app.core.errors.ForbiddenError: the conversation and quote disagree about
        their tenant — impossible through normal code paths, so it is refused loudly
        rather than proceeding with an ambiguous scope.
    """
    quote = quote_service.get_quote_by_token(db, token)
    conversation = get_or_create_conversation(db, quote)

    if conversation.company_id != quote.company_id:
        logger.error(
            "Tenant mismatch: conversation %s (company %s) vs quote %s (company %s)",
            conversation.id,
            conversation.company_id,
            quote.id,
            quote.company_id,
        )
        raise ForbiddenError("Conversation scope could not be verified")

    return AgentContext.from_conversation(conversation)


def list_transcript(db: Session, context: AgentContext) -> list[Message]:
    """Return one conversation's messages in order — the canonical tenant-safe read.

    ``Message`` carries no ``company_id`` (tenancy is derived, not duplicated), so
    transcript reads must join ``Conversation``. This is the single implementation of
    that join; call sites must not hand-roll it.

    Filtering on ``company_id`` as well as ``conversation_id`` is redundant when the
    context is correct — which is exactly why it is here: it is the layer that still
    holds if a wrong conversation id ever reaches this function.
    """
    return list(
        db.scalars(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.id == context.conversation_id,
                Conversation.company_id == context.company_id,
            )
            .order_by(Message.created_at)
        )
    )
