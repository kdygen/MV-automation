"""Tests for the company knowledge layer (Step 3A).

Covers the model, the search service, the ``search_company_knowledge`` tool, and the
agent loop's use of it. The tenant tests are written as attacks: another company's
knowledge is deliberately made the *best* match for the query, so a passing test means
isolation held against the search, not merely that the data happened not to collide.

No test touches the network: the only model used is :class:`ScriptedModel`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agent import AgentContext, ToolArgumentError, ToolExecutor
from app.agent.fake_model import ScriptedModel, text_response, tool_response
from app.agent.loop import run_agent_turn
from app.agent.tools import MAX_KNOWLEDGE_QUERY_LENGTH, TOOL_REGISTRY, TOOL_SCHEMAS
from app.models import Company, CompanyKnowledge
from app.services import agent as agent_service
from app.services.knowledge import MAX_RESULTS, search_knowledge, tokenize
from tests.test_conversation_models import make_quote_chain

TOOL = "search_company_knowledge"

#: Metadata that must never appear in a knowledge tool result, at any nesting level.
INTERNAL_FIELDS = {
    "id",
    "company_id",
    "is_active",
    "created_at",
    "updated_at",
    "keywords",
    "score",
}


def add_entry(db, company: Company, **overrides) -> CompanyKnowledge:
    """Insert one knowledge entry with sensible defaults."""
    fields = {
        "category": "policy",
        "title": "Test policy",
        "content": "Some policy text.",
        "keywords": None,
        "is_active": True,
    }
    fields.update(overrides)
    entry = CompanyKnowledge(company_id=company.id, **fields)
    db.add(entry)
    db.commit()
    return entry


def make_company(db, slug: str) -> Company:
    company = Company(name=slug.title(), slug=slug, email=f"ops@{slug}.test", settings={})
    db.add(company)
    db.commit()
    return company


@pytest.fixture()
def bound(db, company, knowledge):
    """A company with the demo knowledge base, a quote, and an executor bound to it."""
    lead, request, quote = make_quote_chain(db, company)
    context = agent_service.build_agent_context(db, quote.public_token)
    return {
        "company": company,
        "quote": quote,
        "context": context,
        "executor": ToolExecutor(db, context),
    }


@pytest.fixture()
def empty_bound(db, company):
    """The same wiring, but the company has published no knowledge at all."""
    lead, request, quote = make_quote_chain(db, company)
    context = agent_service.build_agent_context(db, quote.public_token)
    return {
        "company": company,
        "context": context,
        "executor": ToolExecutor(db, context),
    }


def walk_values(node, key=None):
    """Yield (key, value) for every scalar in a nested dict/list structure."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk_values(v, k)
    elif isinstance(node, list | tuple):
        for v in node:
            yield from walk_values(v, key)
    else:
        yield key, node


class TestModel:
    """A: every entry belongs to exactly one company."""

    def test_entry_is_owned_by_one_company(self, db, company) -> None:
        entry = add_entry(db, company, title="Owned entry")
        assert entry.company_id == company.id

    def test_company_id_is_required(self, db) -> None:
        db.add(CompanyKnowledge(category="policy", title="Orphan", content="x"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_title_is_unique_per_company_but_not_across_companies(self, db, company) -> None:
        """Two tenants may publish a 'Cancellation policy'; one tenant may not twice."""
        other = make_company(db, "rival-movers")
        add_entry(db, company, title="Cancellation policy")
        add_entry(db, other, title="Cancellation policy")  # different tenant: allowed

        db.add(
            CompanyKnowledge(
                company_id=company.id, category="policy", title="Cancellation policy", content="dup"
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_entries_cascade_with_their_company(self) -> None:
        """No knowledge outlives the tenant it belongs to.

        Asserted against the declared constraint rather than by deleting a row: the test
        suite runs on SQLite, which does not enforce foreign keys unless the connection
        opts in, so a delete here would prove nothing about PostgreSQL.
        """
        fk = next(iter(CompanyKnowledge.__table__.c.company_id.foreign_keys))
        assert fk.column.table.name == "companies"
        assert fk.ondelete == "CASCADE"
        assert CompanyKnowledge.__table__.c.company_id.nullable is False


class TestTenantIsolation:
    """B: one tenant's search can never reach another tenant's knowledge."""

    def test_search_excludes_other_companies(self, db, company) -> None:
        other = make_company(db, "rival-movers")
        add_entry(
            db,
            other,
            category="insurance",
            title="Certificate of Insurance",
            content="Rival Movers issues COIs the same day.",
            keywords="COI, certificate of insurance",
        )
        # Our company has nothing on the subject — the only match in the whole table
        # belongs to the other tenant.
        assert search_knowledge(db, company.id, "certificate of insurance COI") == []

    def test_best_match_in_another_tenant_is_still_invisible(self, db, company) -> None:
        """Relevance never outranks the tenant predicate."""
        other = make_company(db, "rival-movers")
        add_entry(
            db,
            other,
            title="Piano moving",
            content="Rival Movers moves pianos.",
            keywords="piano, grand piano",
        )
        add_entry(db, company, title="Trash removal", content="We haul away unwanted items.")

        results = search_knowledge(db, company.id, "piano")
        assert results == []

    def test_tool_searches_only_the_bound_company(self, db, company) -> None:
        other = make_company(db, "rival-movers")
        add_entry(
            db,
            other,
            title="Certificate of Insurance",
            content="Secret rival policy.",
            keywords="COI",
        )
        lead, request, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)

        result = ToolExecutor(db, context).execute(TOOL, {"query": "COI insurance certificate"})
        assert result == {"results": []}

    def test_a_forged_context_company_cannot_read_another_tenant(
        self, db, company, knowledge
    ) -> None:
        """Even a context pointing at the wrong tenant reads that tenant, not ours."""
        other = make_company(db, "rival-movers")
        forged = AgentContext(
            company_id=other.id,
            conversation_id=uuid.uuid4(),
            quote_id=uuid.uuid4(),
        )
        assert search_knowledge(db, forged.company_id, "certificate of insurance") == []


class TestActiveFiltering:
    """C: deactivated entries are invisible to the agent."""

    def test_inactive_entries_are_excluded(self, db, company) -> None:
        entry = add_entry(
            db,
            company,
            title="Weekend surcharge",
            content="We add 15% on weekends.",
            keywords="weekend, surcharge, saturday",
        )
        assert [m.title for m in search_knowledge(db, company.id, "weekend surcharge")] == [
            "Weekend surcharge"
        ]

        entry.is_active = False
        db.commit()
        assert search_knowledge(db, company.id, "weekend surcharge") == []

    def test_deactivating_hides_the_entry_from_the_tool(self, db, bound) -> None:
        executor = bound["executor"]
        assert executor.execute(TOOL, {"query": "piano"})["results"]

        for entry in db.scalars(
            select(CompanyKnowledge).where(CompanyKnowledge.company_id == bound["company"].id)
        ):
            entry.is_active = False
        db.commit()

        assert executor.execute(TOOL, {"query": "piano"})["results"] == []


class TestSearchRelevance:
    """D/E: real customer wording finds the right entry, or nothing at all."""

    @pytest.mark.parametrize(
        ("question", "expected_title"),
        [
            ("Do you provide a COI?", "Certificate of Insurance"),
            ("certificate of insurance for my building", "Certificate of Insurance"),
            ("Can you move a piano?", "Pianos and oversized items"),
            ("Do you provide packing boxes?", "Packing materials and packing service"),
            ("Is packing included?", "Packing materials and packing service"),
            ("What is your cancellation policy?", "Cancellation and rescheduling"),
            ("Can I reschedule?", "Cancellation and rescheduling"),
            ("Do you charge extra for stairs?", "Stairs and elevators"),
            ("Do I need to reserve an elevator?", "Stairs and elevators"),
            (
                "What happens if the move takes longer?",
                "If the move takes longer than estimated",
            ),
            ("What areas do you serve?", "Areas we serve"),
            ("Do you require a deposit?", "Payment and deposits"),
        ],
    )
    def test_customer_questions_rank_the_right_entry_first(
        self, db, company, knowledge, question: str, expected_title: str
    ) -> None:
        results = search_knowledge(db, company.id, question)
        assert results, f"no match for {question!r}"
        assert results[0].title == expected_title

    def test_irrelevant_question_returns_nothing(self, db, company, knowledge) -> None:
        for query in (
            "Who won the hockey game last night?",
            "quantum entanglement",
            "Can you recommend a good restaurant nearby?",
        ):
            assert search_knowledge(db, company.id, query) == [], query

    def test_a_single_shared_word_is_still_a_match(self, db, company, knowledge) -> None:
        """Recall is deliberately generous; the prompt, not the search, decides relevance.

        "insurance" appears in one title, so the entry comes back even inside an
        off-topic question. Returning it is correct — suppressing single-token matches
        would also suppress "COI?" and "stairs?", which are the questions customers
        actually ask.
        """
        results = search_knowledge(db, company.id, "Do you sell car insurance for boats?")
        assert [m.title for m in results] == ["Certificate of Insurance"]

    def test_empty_and_stopword_only_queries_return_nothing(self, db, company, knowledge) -> None:
        """A meaningless query must not fall back to an arbitrary entry."""
        for query in ("", "   ", "the and you", "?!", "a b c"):
            assert search_knowledge(db, company.id, query) == []

    def test_results_are_capped(self, db, company) -> None:
        for index in range(MAX_RESULTS + 4):
            add_entry(
                db,
                company,
                title=f"Storage option {index}",
                content="We offer storage.",
                keywords="storage",
            )
        assert len(search_knowledge(db, company.id, "storage")) == MAX_RESULTS

    def test_title_match_outranks_a_passing_body_mention(self, db, company) -> None:
        add_entry(
            db,
            company,
            title="Storage",
            content="Short-term storage is available.",
            keywords=None,
        )
        add_entry(
            db,
            company,
            title="Cancellation policy",
            content="Cancel any time. This does not apply to storage bookings.",
            keywords=None,
        )
        assert search_knowledge(db, company.id, "storage")[0].title == "Storage"

    def test_keywords_bridge_vocabulary_the_title_lacks(self, db, company, knowledge) -> None:
        """The reason curated keywords stand in for embeddings at this scale."""
        results = search_knowledge(db, company.id, "COI")
        assert results[0].title == "Certificate of Insurance"

    def test_ordering_is_deterministic_for_equal_scores(self, db, company) -> None:
        add_entry(db, company, title="B storage", content="storage", keywords="storage")
        add_entry(db, company, title="A storage", content="storage", keywords="storage")
        titles = [m.title for m in search_knowledge(db, company.id, "storage")]
        assert titles == ["A storage", "B storage"]

    def test_tokenizer_folds_simple_plurals(self) -> None:
        assert tokenize("boxes") == tokenize("box")
        assert tokenize("movers") == tokenize("mover")
        assert tokenize("policies") == tokenize("policy")

    def test_stopwords_and_short_tokens_are_dropped(self) -> None:
        assert tokenize("Do you have the piano?") == {"piano"}


class TestToolSchema:
    """F/G: the model-visible contract accepts a query and nothing else."""

    def test_schema_declares_only_query(self) -> None:
        schema = TOOL_REGISTRY[TOOL][0]["input_schema"]
        assert set(schema["properties"]) == {"query"}
        assert schema["required"] == ["query"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["query"]["type"] == "string"

    def test_schema_uses_only_strict_mode_safe_keywords(self) -> None:
        """The length cap is enforced server-side, not declared to the provider.

        Strict mode accepts a subset of JSON Schema; shipping a keyword outside it
        fails the whole request. The schema stays to the shape already proven in
        production.
        """
        schema = TOOL_REGISTRY[TOOL][0]["input_schema"]
        assert set(schema) == {"type", "properties", "required", "additionalProperties"}
        assert set(schema["properties"]["query"]) == {"type", "description"}
        # Every declared property is required — strict mode's own requirement.
        assert set(schema["required"]) == set(schema["properties"])

    def test_tool_is_registered_and_advertised(self) -> None:
        assert TOOL in TOOL_REGISTRY
        assert TOOL in {schema["name"] for schema in TOOL_SCHEMAS}

    @pytest.mark.parametrize(
        "forbidden",
        ["company_id", "quote_id", "conversation_id", "lead_id", "public_token", "id"],
    )
    def test_identifier_injection_is_rejected(self, bound, forbidden: str) -> None:
        with pytest.raises(ToolArgumentError, match="scope is determined by the server"):
            bound["executor"].execute(TOOL, {"query": "piano", forbidden: str(uuid.uuid4())})

    def test_unknown_arguments_are_rejected(self, bound) -> None:
        with pytest.raises(ToolArgumentError):
            bound["executor"].execute(TOOL, {"query": "piano", "limit": 50})

    def test_missing_query_is_rejected(self, bound) -> None:
        with pytest.raises(ToolArgumentError, match="requires argument"):
            bound["executor"].execute(TOOL, {})

    def test_blank_query_is_rejected(self, bound) -> None:
        with pytest.raises(ToolArgumentError):
            bound["executor"].execute(TOOL, {"query": "   "})

    def test_non_string_query_is_rejected(self, bound) -> None:
        with pytest.raises(ToolArgumentError):
            bound["executor"].execute(TOOL, {"query": 42})

    def test_oversized_query_is_rejected(self, bound) -> None:
        with pytest.raises(ToolArgumentError, match="at most"):
            bound["executor"].execute(
                TOOL, {"query": "piano " * (MAX_KNOWLEDGE_QUERY_LENGTH // 2)}
            )

    def test_query_at_the_limit_is_accepted(self, bound) -> None:
        query = ("piano " * 100)[: MAX_KNOWLEDGE_QUERY_LENGTH]
        assert len(query) == MAX_KNOWLEDGE_QUERY_LENGTH
        assert bound["executor"].execute(TOOL, {"query": query})["results"]


class TestToolOutput:
    """H: only the three human-readable fields cross the boundary."""

    def test_output_is_exactly_the_allowlist(self, bound) -> None:
        result = bound["executor"].execute(TOOL, {"query": "certificate of insurance"})
        assert set(result) == {"results"}
        assert result["results"]
        for entry in result["results"]:
            assert set(entry) == {"category", "title", "content"}

    def test_no_internal_metadata_leaks(self, bound) -> None:
        result = bound["executor"].execute(TOOL, {"query": "piano boxes cancellation"})
        leaked = {key for key, _ in walk_values(result) if key in INTERNAL_FIELDS}
        assert leaked == set()

    def test_curated_keywords_are_never_returned(self, bound) -> None:
        """Keywords are a retrieval aid, not customer-facing copy."""
        result = bound["executor"].execute(TOOL, {"query": "COI"})
        rendered = str(result)
        assert "proof of insurance" not in rendered

    def test_no_result_is_an_explicit_empty_list(self, bound) -> None:
        assert bound["executor"].execute(TOOL, {"query": "helicopter charter"}) == {"results": []}

    def test_company_without_knowledge_returns_empty(self, empty_bound) -> None:
        assert empty_bound["executor"].execute(TOOL, {"query": "cancellation policy"}) == {
            "results": []
        }

    def test_result_content_matches_the_stored_entry(self, db, bound) -> None:
        result = bound["executor"].execute(TOOL, {"query": "certificate of insurance"})
        stored = db.scalar(
            select(CompanyKnowledge).where(
                CompanyKnowledge.company_id == bound["company"].id,
                CompanyKnowledge.title == "Certificate of Insurance",
            )
        )
        assert result["results"][0] == {
            "category": stored.category,
            "title": stored.title,
            "content": stored.content,
        }


class TestAgentLoopIntegration:
    """I/J: the loop can call the tool, and an empty result stays empty."""

    def test_agent_can_call_the_tool_and_answer_from_it(self, db, bound) -> None:
        model = ScriptedModel(
            [
                tool_response(TOOL, {"query": "certificate of insurance COI building"}),
                text_response("Yes — we issue a COI at no charge with three days' notice."),
            ]
        )
        result = run_agent_turn(db, bound["context"], "Do you provide a COI?", model)

        assert result.tools_used == (TOOL,)
        assert "COI" in result.reply
        # The tool result the model saw came from the database, not the script.
        tool_result = model.calls[-1].messages[-1].tool_result
        assert tool_result["results"][0]["title"] == "Certificate of Insurance"
        assert "three business days" in tool_result["results"][0]["content"]

    def test_agent_receives_an_empty_result_when_nothing_is_published(
        self, db, empty_bound
    ) -> None:
        """J: with no knowledge, the model is handed emptiness — never a substitute."""
        model = ScriptedModel(
            [
                tool_response(TOOL, {"query": "cancellation policy"}),
                text_response("I don't have that on file — please call the office."),
            ]
        )
        result = run_agent_turn(db, empty_bound["context"], "Can I cancel?", model)

        assert result.tools_used == (TOOL,)
        assert model.calls[-1].messages[-1].tool_result == {"results": []}

    def test_identifier_injection_in_the_loop_aborts_the_turn(self, db, bound) -> None:
        """A boundary violation stops the turn; it is never retried or worked around."""
        model = ScriptedModel(
            [
                tool_response(TOOL, {"query": "piano", "company_id": str(uuid.uuid4())}),
                text_response("Let me check that differently."),
            ]
        )
        with pytest.raises(ToolArgumentError):
            run_agent_turn(db, bound["context"], "Do you move pianos?", model)

    def test_tool_is_advertised_to_the_model(self, db, bound) -> None:
        model = ScriptedModel([text_response("Hi!")])
        run_agent_turn(db, bound["context"], "Hello", model)
        assert TOOL in {tool.name for tool in model.calls[0].tools}


class TestExistingToolsUnchanged:
    """K: widening the dispatch signature did not change the first three tools."""

    def test_zero_argument_tools_still_reject_arguments(self, bound) -> None:
        for name in ("get_quote_summary", "get_move_details", "get_company_info"):
            with pytest.raises(ToolArgumentError):
                bound["executor"].execute(name, {"query": "anything"})

    def test_zero_argument_tools_still_work_with_no_arguments(self, bound) -> None:
        quote = bound["executor"].execute("get_quote_summary")
        assert quote["amount_min_cents"] == bound["quote"].amount_min_cents
        assert bound["executor"].execute("get_move_details")["origin"]["city"]
        assert bound["executor"].execute("get_company_info")["name"] == "Acme Movers"

    def test_zero_argument_schemas_are_unchanged(self) -> None:
        for name in ("get_quote_summary", "get_move_details", "get_company_info"):
            schema = TOOL_REGISTRY[name][0]["input_schema"]
            assert schema["properties"] == {}
            assert schema["required"] == []
            assert schema["additionalProperties"] is False


class TestPromptPolicy:
    """The prompt must forbid substituting industry knowledge for company policy."""

    def test_prompt_requires_searching_before_answering_policy(self) -> None:
        from app.agent.prompts import build_system_prompt

        prompt = build_system_prompt().lower()
        assert "search" in prompt
        assert "has not published" in prompt or "not published" in prompt
        assert "moving industry" in prompt
