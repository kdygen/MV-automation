"""Tests for the agent orchestration loop (Step 1C).

Every test runs against a ``ScriptedModel``: no network, no API key, no vendor SDK.
The scripted model simulates the tool-use protocol only, so what these tests actually
prove is the *orchestration* — persistence, transcript replay, the executor boundary,
and the failure modes.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest

from app.agent import (
    AgentContext,
    AgentLoopError,
    ModelProtocolError,
    ToolArgumentError,
    UnknownToolError,
    run_agent_turn,
    to_model_messages,
)
from app.agent.errors import ToolExecutionError
from app.agent.fake_model import (
    ScriptedModel,
    ScriptedModelExhausted,
    malformed_response,
    text_response,
    tool_response,
)
from app.models import Company, Message, MessageRole, MovingRequest
from app.services import agent as agent_service
from tests.test_conversation_models import make_quote_chain


@pytest.fixture()
def bound(db, company):
    """A company with a quote and an agent context bound to its conversation."""
    lead, request, quote = make_quote_chain(db, company)
    context = agent_service.build_agent_context(db, quote.public_token)
    return {
        "company": company,
        "lead": lead,
        "request": request,
        "quote": quote,
        "context": context,
    }


def transcript(db, context) -> list[Message]:
    return agent_service.list_transcript(db, context)


class TestSimpleTurn:
    """Requirements 1–4."""

    def test_final_response_with_no_tools(self, db, bound) -> None:
        model = ScriptedModel([text_response("Happy to help!")])

        result = run_agent_turn(db, bound["context"], "Hello?", model)

        assert result.reply == "Happy to help!"
        assert result.tools_used == ()
        assert result.iterations == 1
        assert model.call_count == 1

    def test_user_message_is_persisted(self, db, bound) -> None:
        model = ScriptedModel([text_response("Yes.")])
        run_agent_turn(db, bound["context"], "  Is packing included?  ", model)

        rows = transcript(db, bound["context"])
        assert rows[0].role is MessageRole.USER
        assert rows[0].content == "Is packing included?"  # trimmed

    def test_assistant_response_is_persisted(self, db, bound) -> None:
        run_agent_turn(db, bound["context"], "Hi", ScriptedModel([text_response("Hello!")]))

        rows = transcript(db, bound["context"])
        assert [r.role for r in rows] == [MessageRole.USER, MessageRole.ASSISTANT]
        assert rows[-1].content == "Hello!"

    def test_token_usage_is_persisted(self, db, bound) -> None:
        model = ScriptedModel([text_response("Hello!", tokens_in=1200, tokens_out=45)])

        result = run_agent_turn(db, bound["context"], "Hi", model)

        assistant = transcript(db, bound["context"])[-1]
        assert assistant.tokens_in == 1200
        assert assistant.tokens_out == 45
        assert result.usage.input_tokens == 1200

    def test_usage_is_aggregated_across_the_whole_turn(self, db, bound) -> None:
        """Intermediate calls are not persisted, so the final row carries turn totals."""
        model = ScriptedModel(
            [
                tool_response("get_quote_summary", tokens_in=900, tokens_out=20),
                text_response("Your quote covers labor and travel.", tokens_in=1100, tokens_out=60),
            ]
        )

        result = run_agent_turn(db, bound["context"], "What's included?", model)

        assistant = transcript(db, bound["context"])[-1]
        assert assistant.tokens_in == 2000  # 900 + 1100
        assert assistant.tokens_out == 80  # 20 + 60
        assert result.usage.input_tokens == 2000

    def test_blank_message_is_rejected(self, db, bound) -> None:
        with pytest.raises(ValueError):
            run_agent_turn(db, bound["context"], "   ", ScriptedModel([text_response("x")]))
        assert transcript(db, bound["context"]) == []


class TestToolLoop:
    """Requirements 5–9."""

    def test_quote_summary_round_trip(self, db, bound) -> None:
        model = ScriptedModel(
            [tool_response("get_quote_summary"), text_response("It's $1,000–$1,300.")]
        )

        result = run_agent_turn(db, bound["context"], "How much is my quote?", model)

        assert result.reply == "It's $1,000–$1,300."
        assert result.tools_used == ("get_quote_summary",)
        assert result.iterations == 2
        # The tool result was fed back to the model on the second call.
        second_call = model.calls[1]
        tool_msgs = [m for m in second_call.messages if m.role == "tool"]
        assert tool_msgs[-1].tool_result["amount_min_cents"] == bound["quote"].amount_min_cents

    def test_tool_invocation_is_persisted(self, db, bound) -> None:
        run_agent_turn(
            db,
            bound["context"],
            "How much?",
            ScriptedModel([tool_response("get_quote_summary"), text_response("Done.")]),
        )

        rows = transcript(db, bound["context"])
        assert [r.role for r in rows] == [
            MessageRole.USER,
            MessageRole.TOOL,
            MessageRole.ASSISTANT,
        ]
        tool_row = rows[1]
        assert tool_row.tool_name == "get_quote_summary"
        assert tool_row.tool_args == {}
        assert tool_row.tool_result["crew_size"] == bound["quote"].crew_size
        assert tool_row.content is None

    def test_move_details_loop(self, db, bound) -> None:
        model = ScriptedModel(
            [tool_response("get_move_details"), text_response("You're moving on the 15th.")]
        )

        result = run_agent_turn(db, bound["context"], "When is my move?", model)

        assert result.tools_used == ("get_move_details",)
        tool_row = transcript(db, bound["context"])[1]
        assert tool_row.tool_result["origin"]["city"] == bound["request"].origin_city

    def test_company_info_loop(self, db, bound) -> None:
        model = ScriptedModel(
            [tool_response("get_company_info"), text_response("Call us any time.")]
        )

        run_agent_turn(db, bound["context"], "How do I reach you?", model)

        tool_row = transcript(db, bound["context"])[1]
        assert tool_row.tool_result["name"] == bound["company"].name

    def test_multiple_sequential_tool_calls(self, db, bound) -> None:
        model = ScriptedModel(
            [
                tool_response("get_quote_summary"),
                tool_response("get_move_details"),
                tool_response("get_company_info"),
                text_response("Here's everything."),
            ]
        )

        result = run_agent_turn(db, bound["context"], "Tell me everything", model)

        assert result.tools_used == (
            "get_quote_summary",
            "get_move_details",
            "get_company_info",
        )
        assert result.iterations == 4
        rows = transcript(db, bound["context"])
        assert [r.role for r in rows] == [
            MessageRole.USER,
            MessageRole.TOOL,
            MessageRole.TOOL,
            MessageRole.TOOL,
            MessageRole.ASSISTANT,
        ]


class TestTranscriptReplay:
    """Requirements 10–11."""

    def test_previous_turn_is_supplied_on_the_next_turn(self, db, bound) -> None:
        run_agent_turn(
            db, bound["context"], "First question", ScriptedModel([text_response("First answer")])
        )

        second = ScriptedModel([text_response("Second answer")])
        run_agent_turn(db, bound["context"], "Second question", second)

        sent = second.calls[0].messages
        assert [(m.role, m.content) for m in sent] == [
            ("user", "First question"),
            ("assistant", "First answer"),
            ("user", "Second question"),
        ]

    def test_history_with_tools_replays_as_paired_request_and_result(self, db, bound) -> None:
        """A persisted tool row must replay as request + result, or providers reject it."""
        run_agent_turn(
            db,
            bound["context"],
            "How much?",
            ScriptedModel([tool_response("get_quote_summary"), text_response("It's $1,000.")]),
        )

        second = ScriptedModel([text_response("Anything else?")])
        run_agent_turn(db, bound["context"], "Thanks", second)

        roles = [m.role for m in second.calls[0].messages]
        assert roles == ["user", "assistant", "tool", "assistant", "user"]
        request_msg = second.calls[0].messages[1]
        result_msg = second.calls[0].messages[2]
        assert request_msg.tool_calls[0].name == "get_quote_summary"
        # Pairing is what makes the replay protocol-valid.
        assert request_msg.tool_calls[0].id == result_msg.tool_call_id

    def test_conversion_exposes_no_internal_fields(self, db, bound) -> None:
        run_agent_turn(
            db,
            bound["context"],
            "How much?",
            ScriptedModel([tool_response("get_quote_summary"), text_response("ok")]),
        )
        converted = to_model_messages(transcript(db, bound["context"]))

        for message in converted:
            assert not hasattr(message, "id")
            assert not hasattr(message, "conversation_id")
            assert not hasattr(message, "created_at")
            assert not hasattr(message, "tokens_in")


class TestFailureModes:
    """Requirements 12–16."""

    def test_unknown_tool_propagates(self, db, bound) -> None:
        model = ScriptedModel([tool_response("delete_everything"), text_response("unreached")])

        with pytest.raises(UnknownToolError):
            run_agent_turn(db, bound["context"], "hi", model)

        # Not retried: the model was called exactly once.
        assert model.call_count == 1

    def test_forbidden_identifier_propagates(self, db, bound) -> None:
        """A smuggled identifier is a boundary violation, never fed back to the model."""
        model = ScriptedModel(
            [
                tool_response("get_quote_summary", {"company_id": str(uuid.uuid4())}),
                text_response("unreached"),
            ]
        )

        with pytest.raises(ToolArgumentError):
            run_agent_turn(db, bound["context"], "hi", model)

        assert model.call_count == 1
        # No tool row was written, because the call never reached a handler.
        assert [r.role for r in transcript(db, bound["context"])] == [MessageRole.USER]

    def test_tool_execution_failure_propagates(self, db, bound) -> None:
        db.delete(db.get(MovingRequest, bound["request"].id))
        db.commit()
        model = ScriptedModel([tool_response("get_move_details"), text_response("unreached")])

        with pytest.raises(ToolExecutionError):
            run_agent_turn(db, bound["context"], "when?", model)

    def test_malformed_response_is_rejected(self, db, bound) -> None:
        with pytest.raises(ModelProtocolError):
            run_agent_turn(db, bound["context"], "hi", ScriptedModel([malformed_response()]))

    def test_loop_limit_stops_an_infinite_tool_caller(self, db, bound) -> None:
        """A model that never stops calling tools is cut off, not allowed to spin."""
        model = ScriptedModel([tool_response("get_quote_summary")], repeat_last=True)

        with pytest.raises(AgentLoopError):
            run_agent_turn(db, bound["context"], "loop forever", model, max_iterations=5)

        assert model.call_count == 5  # budget spent, not exceeded
        # The attempt is preserved as an audit trail.
        rows = transcript(db, bound["context"])
        assert rows[0].role is MessageRole.USER
        assert sum(r.role is MessageRole.TOOL for r in rows) == 5
        assert all(r.role is not MessageRole.ASSISTANT for r in rows)

    def test_exhausted_script_raises_rather_than_defaulting(self, db, bound) -> None:
        model = ScriptedModel([tool_response("get_quote_summary")])
        with pytest.raises(ScriptedModelExhausted):
            run_agent_turn(db, bound["context"], "hi", model)


class TestSecurityThroughTheLoop:
    """Requirements 17–19."""

    def test_tools_are_only_reachable_through_the_executor(self) -> None:
        """The loop must not import handlers directly, which would bypass the allowlist."""
        source = pathlib.Path("app/agent/loop.py").read_text()
        assert "get_quote_summary" not in source
        assert "get_move_details" not in source
        assert "get_company_info" not in source
        assert "TOOL_REGISTRY" not in source
        assert "executor.execute(" in source

    def test_malicious_tool_call_cannot_bypass_the_executor(self, db, bound) -> None:
        """Every identifier a model might smuggle is refused mid-loop."""
        for forbidden in ("company_id", "quote_id", "conversation_id", "lead_id"):
            model = ScriptedModel(
                [tool_response("get_move_details", {forbidden: "x"})]
            )
            with pytest.raises(ToolArgumentError):
                run_agent_turn(db, bound["context"], "probe", model)

    def test_cross_tenant_isolation_holds_through_orchestration(self, db, company) -> None:
        other = Company(name="Bravo Van Lines", slug="bravo", settings={})
        db.add(other)
        db.commit()
        _, _, quote_a = make_quote_chain(db, company, email="a@acme.test")
        _, _, quote_b = make_quote_chain(db, other, email="b@bravo.test")

        ctx_a = agent_service.build_agent_context(db, quote_a.public_token)
        ctx_b = agent_service.build_agent_context(db, quote_b.public_token)

        model_a = ScriptedModel([tool_response("get_company_info"), text_response("a")])
        model_b = ScriptedModel([tool_response("get_company_info"), text_response("b")])
        run_agent_turn(db, ctx_a, "who are you?", model_a)
        run_agent_turn(db, ctx_b, "who are you?", model_b)

        a_result = [m for m in model_a.calls[1].messages if m.role == "tool"][-1].tool_result
        b_result = [m for m in model_b.calls[1].messages if m.role == "tool"][-1].tool_result
        assert a_result["name"] == "Acme Movers"
        assert b_result["name"] == "Bravo Van Lines"

        # Neither conversation's transcript contains the other's rows.
        assert len(agent_service.list_transcript(db, ctx_a)) == 3
        assert len(agent_service.list_transcript(db, ctx_b)) == 3

    def test_forged_context_cannot_read_another_tenant(self, db, company) -> None:
        other = Company(name="Bravo", slug="bravo", settings={})
        db.add(other)
        db.commit()
        _, _, victim = make_quote_chain(db, other, email="victim@bravo.test")

        forged = AgentContext(
            company_id=company.id, conversation_id=uuid.uuid4(), quote_id=victim.id
        )
        model = ScriptedModel([tool_response("get_quote_summary"), text_response("unreached")])

        with pytest.raises(ToolExecutionError):
            run_agent_turn(db, forged, "steal", model)

    def test_no_internal_ids_reach_the_model(self, db, bound) -> None:
        model = ScriptedModel(
            [
                tool_response("get_quote_summary"),
                tool_response("get_move_details"),
                tool_response("get_company_info"),
                text_response("done"),
            ]
        )
        run_agent_turn(db, bound["context"], "everything", model)

        secrets = {
            str(bound["company"].id),
            str(bound["quote"].id),
            str(bound["lead"].id),
            str(bound["request"].id),
            bound["quote"].public_token,
            str(bound["quote"].pricing_config_id),
        }
        # Inspect everything the model was ever shown.
        for call in model.calls:
            blob = repr(call.messages)
            for secret in secrets:
                assert secret not in blob, f"leaked {secret} to the model"


class TestNoNetwork:
    """Requirement 20."""

    def test_agent_package_imports_no_http_or_vendor_sdk(self) -> None:
        forbidden = ("httpx", "requests", "anthropic", "openai", "urllib.request", "aiohttp")
        for path in sorted(pathlib.Path("app/agent").glob("*.py")):
            source = path.read_text()
            for name in forbidden:
                assert f"import {name}" not in source, f"{path.name} imports {name}"
