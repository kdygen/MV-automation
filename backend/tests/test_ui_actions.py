"""Tests for the UI action contract (Step 4B).

The contract's whole value is that a navigation hint is *only* a name. These tests
assert both halves: that the name reaches the frontend, and that producing it changes
nothing in the database.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import func, select

from app.agent import ToolArgumentError, ToolExecutor
from app.agent.actions import SELECTABLE_ACTIONS, SELECTABLE_VALUES, UiAction
from app.agent.fake_model import ScriptedModel, text_response, tool_response
from app.agent.loop import run_agent_turn
from app.models import Booking, Message, MovingRequest, Quote
from app.services import agent as agent_service
from tests.test_conversation_models import make_quote_chain

TOOL = "suggest_next_step"


@pytest.fixture()
def bound(db, company):
    lead, request, quote = make_quote_chain(db, company)
    context = agent_service.build_agent_context(db, quote.public_token)
    return {
        "company": company,
        "request": request,
        "quote": quote,
        "context": context,
        "executor": ToolExecutor(db, context),
    }


class TestEnum:
    def test_none_is_not_selectable_by_the_model(self) -> None:
        """A turn with no action is expressed by not calling the tool, not by saying so."""
        assert UiAction.NONE not in SELECTABLE_ACTIONS
        assert "none" not in SELECTABLE_VALUES

    def test_the_set_is_small_and_stable(self) -> None:
        assert set(SELECTABLE_VALUES) == {
            "change_date",
            "edit_move",
            "accept_quote",
            "contact_company",
        }

    def test_no_payment_action_exists(self) -> None:
        """Whether money is owed is config plus quote state — never a model decision."""
        assert not any("pay" in value for value in UiAction)


class TestTool:
    @pytest.mark.parametrize("action", SELECTABLE_VALUES)
    def test_each_allowlisted_action_round_trips(self, bound, action: str) -> None:
        assert bound["executor"].execute(TOOL, {"action": action}) == {"action": action}

    def test_output_carries_nothing_but_the_action(self, bound) -> None:
        result = bound["executor"].execute(TOOL, {"action": "change_date"})
        assert set(result) == {"action"}

    @pytest.mark.parametrize("bogus", ["", "none", "delete_account", "PAYMENT", "accept quote"])
    def test_unknown_actions_are_refused(self, bound, bogus: str) -> None:
        with pytest.raises(ToolArgumentError):
            bound["executor"].execute(TOOL, {"action": bogus})

    def test_non_string_action_is_refused(self, bound) -> None:
        with pytest.raises(ToolArgumentError):
            bound["executor"].execute(TOOL, {"action": 7})

    def test_missing_action_is_refused(self, bound) -> None:
        with pytest.raises(ToolArgumentError, match="requires argument"):
            bound["executor"].execute(TOOL, {})

    def test_identifiers_are_still_refused(self, bound) -> None:
        with pytest.raises(ToolArgumentError, match="scope is determined by the server"):
            bound["executor"].execute(
                TOOL, {"action": "accept_quote", "quote_id": str(uuid.uuid4())}
            )

    def test_case_and_padding_are_tolerated(self, bound) -> None:
        assert bound["executor"].execute(TOOL, {"action": "  Change_Date "})["action"] == (
            "change_date"
        )


class TestChangesNothing:
    """The security claim: suggesting an action is inert."""

    def test_suggesting_writes_no_rows(self, db, bound) -> None:
        counts = lambda: {  # noqa: E731 - terse on purpose, used twice
            model.__name__: db.scalar(select(func.count()).select_from(model))
            for model in (Quote, MovingRequest, Booking, Message)
        }
        before = counts()
        for action in SELECTABLE_VALUES:
            bound["executor"].execute(TOOL, {"action": action})
        assert counts() == before

    def test_suggesting_accept_does_not_accept(self, db, bound) -> None:
        status_before = bound["quote"].status
        bound["executor"].execute(TOOL, {"action": "accept_quote"})
        db.refresh(bound["quote"])
        assert bound["quote"].status is status_before
        assert bound["quote"].accepted_at is None
        assert db.scalar(select(func.count()).select_from(Booking)) == 0

    def test_suggesting_change_date_does_not_move_the_move(self, db, bound) -> None:
        before = bound["request"].move_date
        bound["executor"].execute(TOOL, {"action": "change_date"})
        db.refresh(bound["request"])
        assert bound["request"].move_date == before


class TestLoopIntegration:
    def test_action_reaches_the_turn_result(self, db, bound) -> None:
        model = ScriptedModel(
            [
                tool_response(TOOL, {"action": "change_date"}),
                text_response("You can move it using the button below."),
            ]
        )
        result = run_agent_turn(db, bound["context"], "Can I change my date?", model)
        assert result.ui_action is UiAction.CHANGE_DATE

    def test_default_is_none_when_no_action_is_suggested(self, db, bound) -> None:
        model = ScriptedModel([text_response("Your estimate is $1,000-$1,300.")])
        result = run_agent_turn(db, bound["context"], "How much is my quote?", model)
        assert result.ui_action is UiAction.NONE

    def test_last_suggestion_wins(self, db, bound) -> None:
        """A model that changes its mind mid-turn gets the later hint, not both."""
        model = ScriptedModel(
            [
                tool_response(TOOL, {"action": "change_date"}),
                tool_response(TOOL, {"action": "edit_move"}),
                text_response("Use the edit button below."),
            ]
        )
        result = run_agent_turn(db, bound["context"], "Actually my home size is wrong", model)
        assert result.ui_action is UiAction.EDIT_MOVE

    def test_a_bad_action_aborts_the_turn(self, db, bound) -> None:
        model = ScriptedModel(
            [tool_response(TOOL, {"action": "wire_me_money"}), text_response("Sure.")]
        )
        with pytest.raises(ToolArgumentError):
            run_agent_turn(db, bound["context"], "Send me money", model)

    def test_action_does_not_persist_into_the_next_turn(self, db, bound) -> None:
        """Hints are per-turn: a stale button must not linger on an unrelated answer."""
        run_agent_turn(
            db,
            bound["context"],
            "Can I change my date?",
            ScriptedModel([tool_response(TOOL, {"action": "change_date"}), text_response("Yes.")]),
        )
        second = run_agent_turn(
            db,
            bound["context"],
            "How much is my quote?",
            ScriptedModel([text_response("$1,000-$1,300.")]),
        )
        assert second.ui_action is UiAction.NONE

    def test_the_loop_still_names_no_tool(self) -> None:
        """The executor records the hint, so loop.py never learns a tool's name."""
        source = pathlib.Path("app/agent/loop.py").read_text()
        assert TOOL not in source
        assert "executor.suggested_action" in source


class TestChatEndpoint:
    def test_reply_carries_the_action(self, client, app, db, company) -> None:
        from tests.test_chat_api import chat_url, install_model

        lead, request, quote = make_quote_chain(db, company)
        install_model(
            app,
            ScriptedModel(
                [
                    tool_response(TOOL, {"action": "accept_quote"}),
                    text_response("You can book it with the button below."),
                ]
            ),
        )
        body = client.post(chat_url(quote.public_token), json={"message": "I want to book"}).json()
        assert body["ui_action"] == "accept_quote"
        assert set(body) == {"reply", "ui_action"}

    def test_reply_never_exposes_the_tool_name(self, client, app, db, company) -> None:
        from tests.test_chat_api import chat_url, install_model

        lead, request, quote = make_quote_chain(db, company)
        install_model(
            app,
            ScriptedModel(
                [tool_response(TOOL, {"action": "change_date"}), text_response("Use the button.")]
            ),
        )
        raw = client.post(chat_url(quote.public_token), json={"message": "change date"}).text
        assert TOOL not in raw


class TestPromptPolicy:
    """Part 2: the assistant routes, and never narrates the change as done."""

    @pytest.fixture()
    def prompt(self) -> str:
        from app.agent.prompts import build_system_prompt

        return build_system_prompt().lower()

    def test_forbids_claiming_the_action_happened(self, prompt: str) -> None:
        for claim in ["i've moved it", "i've booked it", "i've charged your card"]:
            assert claim in prompt, claim

    def test_tells_the_customer_to_use_the_control(self, prompt: str) -> None:
        assert "button below" in prompt

    def test_covers_every_selectable_action(self, prompt: str) -> None:
        for subject in ["change-date", "edit-details", "booking control", "contact control"]:
            assert subject in prompt, subject

    def test_forbids_predicting_the_new_price(self, prompt: str) -> None:
        assert "never predict what the new" in prompt

    def test_forbids_quoting_a_deposit_amount(self, prompt: str) -> None:
        assert "do not \\nquote a deposit" in prompt or "quote a deposit" in prompt
