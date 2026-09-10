"""Tests for the agent capability boundary: tools, schemas, and the executor (1B).

The security tests are written as attacks: they attempt to widen scope through the
tool interface and assert the attempt fails, rather than merely asserting the happy
path works.
"""

from __future__ import annotations

import uuid

import pytest

from app.agent import (
    AgentContext,
    ToolArgumentError,
    ToolExecutor,
    UnknownToolError,
)
from app.agent.errors import ToolExecutionError
from app.agent.tools import (
    FORBIDDEN_ARGUMENTS,
    TOOL_REGISTRY,
    TOOL_SCHEMAS,
    _as_utc,
)
from app.models import Company, MovingRequest
from app.services import agent as agent_service
from tests.test_conversation_models import make_quote_chain

# Identifiers a model must never be able to pass to any tool.
IDENTIFIER_ARGUMENTS = [
    "company_id",
    "quote_id",
    "conversation_id",
    "lead_id",
    "moving_request_id",
    "pricing_config_id",
    "public_token",
]

# Fields that must never appear in any tool's output, at any nesting level.
LEAKY_FIELDS = {
    "id",
    "company_id",
    "quote_id",
    "moving_request_id",
    "lead_id",
    "pricing_config_id",
    "public_token",
    "engine_version",
    "inputs_snapshot",
    "is_adjusted",
    "raw_payload",
    "settings",
    "slug",
    "total_cents",
}


@pytest.fixture()
def bound(db, company):
    """A company with a quote, its conversation, and an executor bound to it."""
    lead, request, quote = make_quote_chain(db, company)
    context = agent_service.build_agent_context(db, quote.public_token)
    return {
        "company": company,
        "lead": lead,
        "request": request,
        "quote": quote,
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


class TestToolSchemas:
    """Requirements 5–8: the model-visible contract accepts no identifiers."""

    @pytest.mark.parametrize("forbidden", IDENTIFIER_ARGUMENTS)
    def test_no_schema_declares_an_identifier_argument(self, forbidden: str) -> None:
        for schema in TOOL_SCHEMAS:
            assert forbidden not in schema["input_schema"].get("properties", {}), (
                f"{schema['name']} exposes {forbidden} to the model"
            )

    def test_no_tool_accepts_more_than_its_declared_arguments(self) -> None:
        """Unknown keys must fail validation, not be silently ignored."""
        for schema in TOOL_SCHEMAS:
            assert schema["input_schema"]["additionalProperties"] is False

    def test_only_the_knowledge_tool_takes_an_argument(self) -> None:
        """The 1B tools stay zero-argument; 3A's addition is a search string, not an id."""
        with_arguments = {
            schema["name"]: set(schema["input_schema"]["properties"])
            for schema in TOOL_SCHEMAS
            if schema["input_schema"]["properties"]
        }
        assert with_arguments == {"search_company_knowledge": {"query"}}

    def test_schemas_cover_exactly_the_registry(self) -> None:
        assert {s["name"] for s in TOOL_SCHEMAS} == set(TOOL_REGISTRY)
        assert set(TOOL_REGISTRY) == {
            "get_quote_summary",
            "get_move_details",
            "get_company_info",
            "search_company_knowledge",
        }

    def test_every_identifier_is_on_the_forbidden_list(self) -> None:
        assert set(IDENTIFIER_ARGUMENTS) <= FORBIDDEN_ARGUMENTS


class TestGetQuoteSummary:
    """Requirement 2 + 12/13."""

    def test_returns_the_bound_quote_only(self, bound) -> None:
        result = bound["executor"].execute("get_quote_summary")
        quote = bound["quote"]

        assert result["amount_min_cents"] == quote.amount_min_cents
        assert result["amount_max_cents"] == quote.amount_max_cents
        assert result["crew_size"] == quote.crew_size
        assert result["status"] == quote.status.value
        assert result["is_expired"] is False

    def test_output_is_exactly_the_allowlist(self, bound) -> None:
        result = bound["executor"].execute("get_quote_summary")
        assert set(result) == {
            "status",
            "currency",
            "amount_min_cents",
            "amount_max_cents",
            "estimated_hours",
            "crew_size",
            "line_items",
            "valid_until",
            "is_expired",
        }

    def test_line_items_drop_engine_meta(self, db, company) -> None:
        lead, request, quote = make_quote_chain(db, company)
        quote.line_items = [
            {
                "code": "labor",
                "label": "Moving labor",
                "amount_cents": 100_000,
                "meta": {"hourly_rate": 190.0, "crew_size": 3},
            }
        ]
        db.commit()
        context = agent_service.build_agent_context(db, quote.public_token)

        result = ToolExecutor(db, context).execute("get_quote_summary")
        assert result["line_items"] == [
            {"code": "labor", "label": "Moving labor", "amount_cents": 100_000}
        ]


class TestGetMoveDetails:
    """Requirement 3 + PII boundary."""

    def test_returns_the_move_bound_to_the_quote(self, bound) -> None:
        result = bound["executor"].execute("get_move_details")
        request = bound["request"]

        assert result["home_size"] == request.home_size.value
        assert result["distance_miles"] == request.distance_miles
        assert result["origin"]["city"] == request.origin_city
        assert result["destination"]["city"] == request.destination_city

    def test_street_address_is_not_exposed(self, bound) -> None:
        """The agent's view stays no wider than the quote page's existing surface."""
        result = bound["executor"].execute("get_move_details")
        for side in ("origin", "destination"):
            assert "line1" not in result[side]
            assert set(result[side]) == {
                "city",
                "state",
                "zip",
                "floor",
                "has_elevator",
                "stairs_flights",
            }

    def test_missing_move_request_fails_cleanly(self, db, company) -> None:
        lead, request, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)
        db.delete(db.get(MovingRequest, request.id))
        db.commit()

        with pytest.raises(ToolExecutionError):
            ToolExecutor(db, context).execute("get_move_details")


class TestGetCompanyInfo:
    """Requirement 4 + internal-settings exclusion."""

    def test_returns_the_bound_company(self, bound) -> None:
        result = bound["executor"].execute("get_company_info")
        assert result["name"] == bound["company"].name
        assert set(result) == {"name", "phone", "email", "quote_validity_days"}

    def test_internal_settings_are_not_exposed(self, db, company) -> None:
        company.settings = {"quote_review_mode": True, "quote_validity_days": 7}
        db.commit()
        _, _, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)

        result = ToolExecutor(db, context).execute("get_company_info")
        assert result["quote_validity_days"] == 7  # allowlisted
        assert "quote_review_mode" not in result  # operator-only
        assert "settings" not in result


class TestOutputLeakage:
    """Requirement 12/13, applied to every tool at every nesting level."""

    @pytest.mark.parametrize(
        "tool", ["get_quote_summary", "get_move_details", "get_company_info"]
    )
    def test_no_internal_field_names_in_output(self, bound, tool: str) -> None:
        result = bound["executor"].execute(tool)
        for key, _ in walk_values(result):
            assert key not in LEAKY_FIELDS, f"{tool} leaked field {key!r}"

    @pytest.mark.parametrize(
        "tool", ["get_quote_summary", "get_move_details", "get_company_info"]
    )
    def test_no_internal_values_in_output(self, bound, tool: str) -> None:
        """Even under a different key name, these values must not appear."""
        secrets = {
            str(bound["company"].id),
            str(bound["quote"].id),
            str(bound["lead"].id),
            str(bound["request"].id),
            bound["quote"].public_token,
            str(bound["quote"].pricing_config_id),
        }
        result = bound["executor"].execute(tool)
        for _, value in walk_values(result):
            assert str(value) not in secrets, f"{tool} leaked {value!r}"


class TestExecutorGuards:
    """Requirements 9 and 11: the model cannot steer scope or reach other code."""

    def test_unknown_tool_is_rejected(self, bound) -> None:
        with pytest.raises(UnknownToolError):
            bound["executor"].execute("drop_all_tables")

    def test_service_and_model_names_are_not_callable(self, bound) -> None:
        for name in ("accept_quote", "Quote", "app.services.quotes", "__init__"):
            with pytest.raises(UnknownToolError):
                bound["executor"].execute(name)

    @pytest.mark.parametrize("forbidden", IDENTIFIER_ARGUMENTS)
    def test_supplying_an_identifier_is_refused(self, bound, forbidden: str) -> None:
        with pytest.raises(ToolArgumentError):
            bound["executor"].execute("get_quote_summary", {forbidden: str(uuid.uuid4())})

    def test_unexpected_argument_is_refused(self, bound) -> None:
        with pytest.raises(ToolArgumentError):
            bound["executor"].execute("get_move_details", {"verbosity": "high"})

    def test_hallucinated_id_cannot_widen_scope(self, db, company) -> None:
        """The classic attack: name another tenant's quote in the arguments."""
        other = Company(name="Bravo", slug="bravo", settings={})
        db.add(other)
        db.commit()
        _, _, victim_quote = make_quote_chain(db, other, email="victim@bravo.test")
        _, _, own_quote = make_quote_chain(db, company, email="me@acme.test")

        context = agent_service.build_agent_context(db, own_quote.public_token)
        executor = ToolExecutor(db, context)

        with pytest.raises(ToolArgumentError):
            executor.execute("get_quote_summary", {"quote_id": str(victim_quote.id)})

        # And the tool still returns only the caller's own quote.
        result = executor.execute("get_quote_summary")
        assert result["amount_min_cents"] == own_quote.amount_min_cents


class TestCrossTenantIsolation:
    """Requirement 10: isolation holds even against a forged context."""

    def test_forged_company_id_reads_nothing(self, db, company) -> None:
        other = Company(name="Bravo", slug="bravo", settings={})
        db.add(other)
        db.commit()
        _, _, victim_quote = make_quote_chain(db, other, email="victim@bravo.test")

        # Attacker knows the victim's quote id but is scoped to their own tenant.
        forged = AgentContext(
            company_id=company.id,
            conversation_id=uuid.uuid4(),
            quote_id=victim_quote.id,
        )
        with pytest.raises(ToolExecutionError):
            ToolExecutor(db, forged).execute("get_quote_summary")

    def test_each_context_sees_only_its_own_tenant(self, db, company) -> None:
        other = Company(name="Bravo Van Lines", slug="bravo", settings={})
        db.add(other)
        db.commit()
        _, _, quote_a = make_quote_chain(db, company, email="a@acme.test")
        _, _, quote_b = make_quote_chain(db, other, email="b@bravo.test")

        ctx_a = agent_service.build_agent_context(db, quote_a.public_token)
        ctx_b = agent_service.build_agent_context(db, quote_b.public_token)

        assert ToolExecutor(db, ctx_a).execute("get_company_info")["name"] == "Acme Movers"
        assert (
            ToolExecutor(db, ctx_b).execute("get_company_info")["name"] == "Bravo Van Lines"
        )


class TestReadOnly:
    def test_tools_do_not_write(self, bound, db) -> None:
        """Running every tool must leave the database untouched.

        ``valid_until`` is compared through ``_as_utc`` because SQLite returns
        datetimes naive on re-read — the same normalization the quote service uses.
        """
        quote = bound["quote"]
        before = (quote.status, quote.amount_min_cents, _as_utc(quote.valid_until))

        # Arguments per tool: only the knowledge search takes one.
        for name in TOOL_REGISTRY:
            arguments = {"query": "cancellation"} if name == "search_company_knowledge" else None
            bound["executor"].execute(name, arguments)

        db.expire_all()
        refreshed = db.get(type(quote), quote.id)
        assert (
            refreshed.status,
            refreshed.amount_min_cents,
            _as_utc(refreshed.valid_until),
        ) == before
