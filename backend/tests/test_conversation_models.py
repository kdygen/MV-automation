"""Model tests for the agent's conversation persistence (Step 1A).

Covers the invariants the post-quote agent will depend on: one thread per quote,
tenant ownership derived (never duplicated), chronological transcripts, nullable
tool/usage columns, and cascade cleanup.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Company,
    Conversation,
    ConversationStatus,
    Lead,
    Message,
    MessageRole,
    MovingRequest,
    PricingConfigRow,
    Quote,
    QuoteStatus,
)
from app.models.moving_request import HomeSize
from app.pricing import PricingConfig


def make_quote_chain(db, company: Company, *, email: str = "bob@example.com"):
    """Create the lead → request → quote chain a conversation attaches to."""
    lead = Lead(company_id=company.id, name="Bob", email=email)
    db.add(lead)
    db.flush()

    request = MovingRequest(
        company_id=company.id,
        lead_id=lead.id,
        origin_line1="12 Elm St",
        origin_city="Springfield",
        origin_state="IL",
        origin_zip="62701",
        destination_line1="99 Oak Ave",
        destination_city="Chatham",
        destination_state="IL",
        destination_zip="62629",
        move_date=date.today() + timedelta(days=30),
        home_size=HomeSize.TWO_BR,
        special_items=[],
        distance_miles=12.0,
        raw_payload={},
    )
    config = PricingConfigRow(
        company_id=company.id,
        version=1,
        is_active=True,
        config=PricingConfig().model_dump(mode="json"),
    )
    db.add_all([request, config])
    db.flush()

    quote = Quote(
        company_id=company.id,
        moving_request_id=request.id,
        pricing_config_id=config.id,
        status=QuoteStatus.SENT,
        amount_min_cents=100_000,
        amount_max_cents=130_000,
        total_cents=115_000,
        estimated_hours=6.0,
        crew_size=3,
        engine_version="rules-v1.0",
        line_items=[],
        inputs_snapshot={},
        public_token=uuid.uuid4().hex,
        valid_until=date.today() + timedelta(days=14),
    )
    db.add(quote)
    db.commit()
    return lead, request, quote


@pytest.fixture()
def quote_chain(db, company):
    return make_quote_chain(db, company)


class TestConversation:
    def test_created_with_defaults(self, db, company, quote_chain) -> None:
        _, _, quote = quote_chain
        convo = Conversation(company_id=company.id, quote_id=quote.id)
        db.add(convo)
        db.commit()

        assert isinstance(convo.id, uuid.UUID)
        assert convo.status is ConversationStatus.ACTIVE  # default
        assert convo.summary is None  # nothing writes it in 1A
        assert convo.created_at is not None and convo.updated_at is not None

    def test_one_conversation_per_quote(self, db, company, quote_chain) -> None:
        """UNIQUE(quote_id) is what makes get-or-create race-safe."""
        _, _, quote = quote_chain
        db.add(Conversation(company_id=company.id, quote_id=quote.id))
        db.commit()

        db.add(Conversation(company_id=company.id, quote_id=quote.id))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_status_roundtrips_as_enum(self, db, company, quote_chain) -> None:
        _, _, quote = quote_chain
        convo = Conversation(
            company_id=company.id, quote_id=quote.id, status=ConversationStatus.CLOSED
        )
        db.add(convo)
        db.commit()
        db.expire_all()
        assert db.get(Conversation, convo.id).status is ConversationStatus.CLOSED

    def test_requires_a_quote(self, db, company) -> None:
        """V1 is post-quote only: a conversation without a quote must not exist."""
        db.add(Conversation(company_id=company.id))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_lead_is_reachable_without_being_stored(self, db, company, quote_chain) -> None:
        """The lead is derived (quote → request → lead), not duplicated on the row."""
        lead, request, quote = quote_chain
        convo = Conversation(company_id=company.id, quote_id=quote.id)
        db.add(convo)
        db.commit()

        assert not hasattr(convo, "lead_id")
        resolved_lead = db.scalar(
            select(Lead)
            .join(MovingRequest, MovingRequest.lead_id == Lead.id)
            .join(Quote, Quote.moving_request_id == MovingRequest.id)
            .join(Conversation, Conversation.quote_id == Quote.id)
            .where(Conversation.id == convo.id)
        )
        assert resolved_lead is not None and resolved_lead.id == lead.id


class TestMessages:
    def test_transcript_is_ordered_and_tool_fields_optional(
        self, db, company, quote_chain
    ) -> None:
        _, _, quote = quote_chain
        convo = Conversation(company_id=company.id, quote_id=quote.id)
        db.add(convo)
        db.flush()

        db.add_all(
            [
                Message(
                    conversation_id=convo.id,
                    role=MessageRole.USER,
                    content="Does the price include packing?",
                ),
                Message(
                    conversation_id=convo.id,
                    role=MessageRole.TOOL,
                    tool_name="get_quote_summary",
                    tool_args={},
                    tool_result={"amount_min_cents": 100_000},
                ),
                Message(
                    conversation_id=convo.id,
                    role=MessageRole.ASSISTANT,
                    content="Your quote covers labor and travel.",
                    tokens_in=850,
                    tokens_out=120,
                ),
            ]
        )
        db.commit()
        db.expire_all()

        messages = db.get(Conversation, convo.id).messages
        assert [m.role for m in messages] == [
            MessageRole.USER,
            MessageRole.TOOL,
            MessageRole.ASSISTANT,
        ]
        # User turn: no tool fields, no usage.
        assert messages[0].tool_name is None and messages[0].tokens_in is None
        # Tool turn: no prose, payload preserved for audit.
        assert messages[1].content is None
        assert messages[1].tool_result == {"amount_min_cents": 100_000}
        # Assistant turn: usage metered.
        assert messages[2].tokens_in == 850 and messages[2].tokens_out == 120

    def test_message_requires_a_conversation(self, db, company) -> None:
        """A transcript row with no parent has no tenant — must be impossible."""
        db.add(Message(role=MessageRole.USER, content="orphan"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_tenant_is_derived_from_the_parent(self, db, company, quote_chain) -> None:
        """Messages carry no company_id; ownership comes from the conversation."""
        _, _, quote = quote_chain
        convo = Conversation(company_id=company.id, quote_id=quote.id)
        db.add(convo)
        db.flush()
        db.add(Message(conversation_id=convo.id, role=MessageRole.USER, content="hi"))
        db.commit()

        message = db.scalar(select(Message))
        assert not hasattr(message, "company_id")
        assert message.conversation.company_id == company.id

    def test_deleting_conversation_cascades_to_messages(
        self, db, company, quote_chain
    ) -> None:
        _, _, quote = quote_chain
        convo = Conversation(company_id=company.id, quote_id=quote.id)
        db.add(convo)
        db.flush()
        db.add(Message(conversation_id=convo.id, role=MessageRole.USER, content="hi"))
        db.commit()

        db.delete(convo)
        db.commit()
        assert db.scalars(select(Message)).all() == []


class TestTenantScoping:
    def test_conversations_are_isolated_per_company(self, db, company) -> None:
        """A query filtered by company_id sees only that tenant's threads."""
        other = Company(name="Bravo Van Lines", slug="bravo-van-lines", settings={})
        db.add(other)
        db.commit()

        _, _, quote_a = make_quote_chain(db, company, email="a@acme.test")
        _, _, quote_b = make_quote_chain(db, other, email="b@bravo.test")
        db.add_all(
            [
                Conversation(company_id=company.id, quote_id=quote_a.id),
                Conversation(company_id=other.id, quote_id=quote_b.id),
            ]
        )
        db.commit()

        acme_threads = db.scalars(
            select(Conversation).where(Conversation.company_id == company.id)
        ).all()
        assert len(acme_threads) == 1
        assert acme_threads[0].quote_id == quote_a.id

    def test_messages_are_tenant_filtered_by_joining_conversation(
        self, db, company
    ) -> None:
        """The read pattern replacing Message.company_id: join through the parent."""
        other = Company(name="Bravo Van Lines", slug="bravo-van-lines", settings={})
        db.add(other)
        db.commit()

        _, _, quote_a = make_quote_chain(db, company, email="a@acme.test")
        _, _, quote_b = make_quote_chain(db, other, email="b@bravo.test")
        convo_a = Conversation(company_id=company.id, quote_id=quote_a.id)
        convo_b = Conversation(company_id=other.id, quote_id=quote_b.id)
        db.add_all([convo_a, convo_b])
        db.flush()
        db.add_all(
            [
                Message(conversation_id=convo_a.id, role=MessageRole.USER, content="acme msg"),
                Message(conversation_id=convo_b.id, role=MessageRole.USER, content="bravo msg"),
            ]
        )
        db.commit()

        acme_messages = db.scalars(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.company_id == company.id)
        ).all()
        assert [m.content for m in acme_messages] == ["acme msg"]
