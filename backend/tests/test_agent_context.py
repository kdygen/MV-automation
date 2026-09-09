"""Tests for AgentContext construction and the tenant-safe transcript helper (1B)."""

from __future__ import annotations

import dataclasses
import uuid

import pytest
from sqlalchemy import select

from app.agent import AgentContext
from app.core.errors import ForbiddenError, NotFoundError
from app.models import Company, Conversation, Message, MessageRole
from app.services import agent as agent_service
from tests.test_conversation_models import make_quote_chain


class TestBuildContext:
    def test_binds_company_conversation_and_quote(self, db, company) -> None:
        _, _, quote = make_quote_chain(db, company)

        context = agent_service.build_agent_context(db, quote.public_token)

        assert context.company_id == company.id
        assert context.quote_id == quote.id
        conversation = db.scalar(select(Conversation))
        assert context.conversation_id == conversation.id

    def test_is_frozen(self, db, company) -> None:
        """A handler must not be able to reassign the tenant mid-turn."""
        _, _, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)

        with pytest.raises(dataclasses.FrozenInstanceError):
            context.company_id = uuid.uuid4()  # type: ignore[misc]

    def test_holds_identifiers_not_orm_objects(self, db, company) -> None:
        """No ORM objects on the context — nothing traversable to sibling rows."""
        _, _, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)

        for value in (context.company_id, context.conversation_id, context.quote_id):
            assert isinstance(value, uuid.UUID)
        assert not hasattr(context, "quote")
        assert not hasattr(context, "company")

    def test_second_call_reuses_the_same_conversation(self, db, company) -> None:
        _, _, quote = make_quote_chain(db, company)

        first = agent_service.build_agent_context(db, quote.public_token)
        second = agent_service.build_agent_context(db, quote.public_token)

        assert first == second
        assert len(db.scalars(select(Conversation)).all()) == 1

    def test_unknown_token_is_not_found(self, db, company) -> None:
        with pytest.raises(NotFoundError):
            agent_service.build_agent_context(db, "no-such-token")

    def test_tenant_mismatch_is_refused(self, db, company) -> None:
        """A conversation whose company disagrees with its quote must not yield scope."""
        other = Company(name="Bravo", slug="bravo", settings={})
        db.add(other)
        db.commit()
        _, _, quote = make_quote_chain(db, company)

        # Force the inconsistent state a bug (or tampering) could produce.
        conversation = Conversation(company_id=other.id, quote_id=quote.id)
        db.add(conversation)
        db.commit()

        with pytest.raises(ForbiddenError):
            agent_service.build_agent_context(db, quote.public_token)


class TestTranscriptHelper:
    def test_returns_only_the_bound_conversation_in_order(self, db, company) -> None:
        _, _, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)
        db.add_all(
            [
                Message(
                    conversation_id=context.conversation_id,
                    role=MessageRole.USER,
                    content="first",
                ),
                Message(
                    conversation_id=context.conversation_id,
                    role=MessageRole.ASSISTANT,
                    content="second",
                ),
            ]
        )
        db.commit()

        transcript = agent_service.list_transcript(db, context)
        assert [m.content for m in transcript] == ["first", "second"]

    def test_cross_tenant_transcript_is_empty(self, db, company) -> None:
        """The company_id predicate holds even when the conversation id is right."""
        other = Company(name="Bravo", slug="bravo", settings={})
        db.add(other)
        db.commit()
        _, _, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)
        db.add(
            Message(
                conversation_id=context.conversation_id,
                role=MessageRole.USER,
                content="acme only",
            )
        )
        db.commit()

        # Same thread id, attacker's tenant: the join must yield nothing.
        forged = AgentContext(
            company_id=other.id,
            conversation_id=context.conversation_id,
            quote_id=context.quote_id,
        )
        assert agent_service.list_transcript(db, forged) == []
