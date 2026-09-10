"""Endpoint tests for the public post-quote chat API (Step 2B).

The chat model is injected as a ``ScriptedModel`` (Step 1C), so these tests exercise
the real endpoint, real agent loop, real ToolExecutor and real database — with **no
network call and no API key**. What is proven is the HTTP contract, the safety of what
leaves the server, and that the Step 1B boundary still holds through the HTTP layer.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.agent.errors import (
    AgentLoopError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from app.agent.fake_model import ScriptedModel, text_response, tool_response
from app.api.v1.chat import chat_model_dep
from app.models import Company, Conversation, Message, MessageRole
from app.schemas.chat import MAX_MESSAGE_LENGTH
from tests.test_conversation_models import make_quote_chain


def chat_url(token: str) -> str:
    return f"/api/v1/public/quotes/{token}/chat"


def install_model(app, model) -> None:
    app.dependency_overrides[chat_model_dep] = lambda: model


class ExplodingModel:
    """A model that always raises, to exercise provider failure mapping."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def complete(self, *, system, messages, tools):  # type: ignore[no-untyped-def]
        raise self._error


@pytest.fixture()
def quote(db, company):
    _, _, quote = make_quote_chain(db, company)
    return quote


@pytest.fixture()
def scripted(app):
    """Default happy-path script: one tool call, then a final answer."""
    model = ScriptedModel(
        [tool_response("get_quote_summary"), text_response("Your estimate is $1,000–$1,300.")]
    )
    install_model(app, model)
    return model


class TestHappyPath:
    """Requirement A."""

    def test_valid_token_and_message_returns_reply(self, client, quote, scripted) -> None:
        resp = client.post(chat_url(quote.public_token), json={"message": "How much is my quote?"})

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"reply": "Your estimate is $1,000–$1,300."}

    def test_message_is_trimmed_before_reaching_the_agent(
        self, client, db, quote, scripted
    ) -> None:
        client.post(chat_url(quote.public_token), json={"message": "  spaced out  "})

        stored = db.scalars(select(Message).where(Message.role == MessageRole.USER)).all()
        assert [m.content for m in stored] == ["spaced out"]


class TestConversationLifecycle:
    """Requirements B and C."""

    def test_first_request_creates_the_conversation(
        self, client, db, quote, scripted
    ) -> None:
        assert db.scalars(select(Conversation)).all() == []

        client.post(chat_url(quote.public_token), json={"message": "hi"})

        conversations = db.scalars(select(Conversation)).all()
        assert len(conversations) == 1
        assert conversations[0].quote_id == quote.id

    def test_second_request_reuses_the_same_conversation_and_history(
        self, client, app, db, quote
    ) -> None:
        install_model(app, ScriptedModel([text_response("First answer.")]))
        client.post(chat_url(quote.public_token), json={"message": "First question"})

        second = ScriptedModel([text_response("Second answer.")])
        install_model(app, second)
        resp = client.post(chat_url(quote.public_token), json={"message": "Second question"})

        assert resp.json()["reply"] == "Second answer."
        # One conversation, and the earlier turn was replayed to the model.
        assert len(db.scalars(select(Conversation)).all()) == 1
        sent = [(m.role, m.content) for m in second.calls[0].messages]
        assert sent == [
            ("user", "First question"),
            ("assistant", "First answer."),
            ("user", "Second question"),
        ]

    def test_messages_persist_in_order(self, client, db, quote, scripted) -> None:
        client.post(chat_url(quote.public_token), json={"message": "How much?"})

        rows = db.scalars(select(Message).order_by(Message.created_at)).all()
        assert [r.role for r in rows] == [
            MessageRole.USER,
            MessageRole.TOOL,
            MessageRole.ASSISTANT,
        ]
        assert rows[1].tool_name == "get_quote_summary"


class TestRequestValidation:
    """Requirements E, F, G."""

    def test_unknown_token_is_404(self, client, company, scripted) -> None:
        resp = client.post(chat_url("no-such-token"), json={"message": "hello"})

        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    def test_blank_message_is_rejected(self, client, quote, scripted, blank: str) -> None:
        resp = client.post(chat_url(quote.public_token), json={"message": blank})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"

    def test_missing_message_is_rejected(self, client, quote, scripted) -> None:
        assert client.post(chat_url(quote.public_token), json={}).status_code == 422

    def test_oversized_message_is_rejected_not_truncated(
        self, client, db, quote, scripted
    ) -> None:
        resp = client.post(
            chat_url(quote.public_token), json={"message": "x" * (MAX_MESSAGE_LENGTH + 1)}
        )
        assert resp.status_code == 422
        # Nothing was persisted, so nothing was silently half-answered.
        assert db.scalars(select(Message)).all() == []

    def test_message_at_the_limit_is_accepted(self, client, quote, scripted) -> None:
        resp = client.post(chat_url(quote.public_token), json={"message": "x" * MAX_MESSAGE_LENGTH})
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "identifier",
        ["company_id", "quote_id", "conversation_id", "lead_id", "moving_request_id"],
    )
    def test_identifiers_in_the_body_are_rejected(
        self, client, quote, scripted, identifier: str
    ) -> None:
        """extra='forbid' means injection is refused, not silently ignored."""
        resp = client.post(
            chat_url(quote.public_token),
            json={"message": "hello", identifier: str(uuid.uuid4())},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


class TestResponseLeakage:
    """Requirement H."""

    def test_response_contains_only_the_reply(self, client, quote, scripted) -> None:
        body = client.post(chat_url(quote.public_token), json={"message": "How much?"}).json()
        assert set(body) == {"reply"}

    def test_response_leaks_no_identifiers_tools_or_token(
        self, client, app, db, company, quote
    ) -> None:
        install_model(
            app, ScriptedModel([tool_response("get_quote_summary"), text_response("All good.")])
        )

        raw = client.post(chat_url(quote.public_token), json={"message": "How much?"}).text

        conversation = db.scalar(select(Conversation))
        secrets = [
            str(company.id),
            str(quote.id),
            str(quote.pricing_config_id),
            str(conversation.id),
            quote.public_token,
            "get_quote_summary",  # tool names are internal architecture
            "amount_min_cents",  # raw tool result fields
        ]
        for secret in secrets:
            assert secret not in raw, f"response leaked {secret!r}"


class TestFailureMapping:
    """Requirements I and J."""

    @pytest.mark.parametrize(
        ("error", "expected_status", "expected_code"),
        [
            (ProviderRateLimitError("busy"), 429, "rate_limited"),
            (ProviderAuthError("bad key"), 503, "service_unavailable"),
            (ProviderResponseError("garbage"), 502, "upstream_error"),
            (AgentLoopError("no convergence"), 502, "upstream_error"),
        ],
    )
    def test_provider_failures_map_to_safe_statuses(
        self, client, app, quote, error, expected_status, expected_code
    ) -> None:
        install_model(app, ExplodingModel(error))

        resp = client.post(chat_url(quote.public_token), json={"message": "hello"})

        assert resp.status_code == expected_status
        body = resp.json()
        assert body["error"]["code"] == expected_code
        # The provider's own words never reach the customer.
        for internal in ("bad key", "garbage", "no convergence", "openai", "sk-"):
            assert internal.lower() not in resp.text.lower()

    def test_security_boundary_failure_does_not_leak_internals(self, client, app, quote) -> None:
        """A model smuggling an identifier fails closed, with nothing explained."""
        install_model(
            app,
            ScriptedModel([tool_response("get_quote_summary", {"company_id": str(uuid.uuid4())})]),
        )

        resp = client.post(chat_url(quote.public_token), json={"message": "probe"})

        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "upstream_error"
        for leak in ("company_id", "get_quote_summary", "ToolArgumentError"):
            assert leak not in resp.text

    def test_unknown_tool_also_fails_closed(self, client, app, quote) -> None:
        install_model(app, ScriptedModel([tool_response("delete_everything")]))

        resp = client.post(chat_url(quote.public_token), json={"message": "probe"})

        assert resp.status_code == 502
        assert "delete_everything" not in resp.text

    def test_unexpected_error_is_generic(self, client, app, quote) -> None:
        install_model(app, ExplodingModel(RuntimeError("secret internal detail")))

        resp = client.post(chat_url(quote.public_token), json={"message": "hello"})

        assert resp.status_code == 502
        assert "secret internal detail" not in resp.text
        assert "Traceback" not in resp.text


class TestTenantIsolation:
    """Requirement K."""

    def test_each_token_reaches_only_its_own_company(self, client, app, db, company) -> None:
        other = Company(name="Bravo Van Lines", slug="bravo", settings={})
        db.add(other)
        db.commit()
        _, _, own = make_quote_chain(db, company, email="a@acme.test")
        _, _, victim = make_quote_chain(db, other, email="b@bravo.test")

        acme_model = ScriptedModel([tool_response("get_company_info"), text_response("acme")])
        install_model(app, acme_model)
        client.post(chat_url(own.public_token), json={"message": "who are you?"})

        bravo_model = ScriptedModel([tool_response("get_company_info"), text_response("bravo")])
        install_model(app, bravo_model)
        client.post(chat_url(victim.public_token), json={"message": "who are you?"})

        def tool_payload(model):
            return [m for m in model.calls[1].messages if m.role == "tool"][-1].tool_result

        assert tool_payload(acme_model)["name"] == "Acme Movers"
        assert tool_payload(bravo_model)["name"] == "Bravo Van Lines"
        # Two separate conversations, one per quote.
        assert len(db.scalars(select(Conversation)).all()) == 2


class TestRateLimiting:
    """Requirement L."""

    def test_per_ip_limit_returns_429(self, client, app, db, company, test_settings) -> None:
        test_settings.rate_limit_enabled = True
        _, _, quote = make_quote_chain(db, company)
        install_model(app, ScriptedModel([text_response("ok")], repeat_last=True))

        # Per-IP budget is 10 per 10 minutes.
        for i in range(10):
            resp = client.post(chat_url(quote.public_token), json={"message": f"msg {i}"})
            assert resp.status_code == 200, f"request {i + 1}: {resp.text}"

        resp = client.post(chat_url(quote.public_token), json={"message": "one too many"})
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "rate_limited"

    def test_per_token_limit_survives_ip_rotation(
        self, client, app, db, company, test_settings
    ) -> None:
        """The point of the token limit: changing IP must not buy more model calls."""
        test_settings.rate_limit_enabled = True
        _, _, quote = make_quote_chain(db, company)
        install_model(app, ScriptedModel([text_response("ok")], repeat_last=True))

        # A fresh IP each time defeats the per-IP limiter entirely.
        statuses = []
        for i in range(22):
            statuses.append(
                client.post(
                    chat_url(quote.public_token),
                    json={"message": f"msg {i}"},
                    headers={"X-Forwarded-For": f"203.0.113.{i + 1}"},
                ).status_code
            )

        assert statuses[:20] == [200] * 20  # per-token budget
        assert statuses[20:] == [429, 429]  # then the token itself is throttled


class TestNoNetworkAndExistingEndpoints:
    """Requirements M and N."""

    def test_chat_tests_need_no_api_key(self, test_settings) -> None:
        assert test_settings.openai_api_key == ""

    def test_existing_quote_endpoints_still_work(self, client, db, company, quote) -> None:
        view = client.get(f"/api/v1/public/quotes/{quote.public_token}")
        assert view.status_code == 200
        assert view.json()["company_name"] == "Acme Movers"

        accept = client.post(f"/api/v1/public/quotes/{quote.public_token}/accept")
        assert accept.status_code == 200
        assert accept.json()["status"] == "confirmed"
