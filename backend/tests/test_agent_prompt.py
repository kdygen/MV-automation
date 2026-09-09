"""Policy tests for the V1 system prompt.

These assert on the *prompt's* content, never on model output — GPT wording varies
run to run, so an exact-output test would be brittle and would fail for reasons that
have nothing to do with policy. What is pinned here is that each rule we rely on is
actually stated, and that the prompt does not itself instruct the assistant to do
something it cannot do.
"""

from __future__ import annotations

import re

from app.agent import build_system_prompt
from app.agent.prompts import SYSTEM_PROMPT


def prompt() -> str:
    """Lower-cased, whitespace-normalized so assertions survive line re-wrapping."""
    return re.sub(r"\s+", " ", build_system_prompt().lower())


class TestNoUnsupportedCapabilities:
    """Observed V1 failure: the assistant offered to relay messages to staff."""

    def test_prompt_forbids_relaying_and_escalating(self) -> None:
        text = prompt()
        # The prohibition must be explicit, not merely implied.
        assert "never offer or imply that you can" in text
        for capability in (
            "pass along a message",
            "escalate",
            "arrange a callback",
            "get back to the customer later",
        ):
            assert capability in text, f"prompt does not forbid: {capability}"

    def test_prompt_states_it_cannot_reach_anyone(self) -> None:
        assert "no way to reach anyone at the company" in prompt()

    def test_prompt_never_instructs_the_assistant_to_relay(self) -> None:
        """The old prompt told it to 'offer to pass the question to the team'."""
        text = prompt()
        forbidden_instructions = [
            "offer to pass the question",
            "offer to pass it on",
            "pass the question to the team",
            "i can contact",
            "offer to contact",
        ]
        for phrase in forbidden_instructions:
            assert phrase not in text, f"prompt still instructs relaying: {phrase!r}"

    def test_prompt_directs_customers_to_company_contact_details(self) -> None:
        """The supported fallback: share contact info, let the customer reach out."""
        text = prompt()
        assert "contact details" in text
        assert "reach out themselves" in text

    def test_prompt_still_states_read_only_limits(self) -> None:
        text = prompt()
        for action in ("change the quote", "accept or decline", "book the move", "take payment"):
            assert action in text


class TestToolGrounding:
    """Observed V1 failure: answering current facts from the transcript."""

    def test_prompt_requires_tools_for_factual_details(self) -> None:
        text = prompt()
        assert "use your tools for any factual detail" in text
        assert "never answer these from memory or guess" in text

    def test_prompt_prefers_tools_over_earlier_conversation(self) -> None:
        text = prompt()
        assert "even if the information came up earlier in this conversation" in text
        # Transcript is context, not authoritative state.
        assert "context, not current facts" in text

    def test_prompt_names_the_mutable_facts_that_need_a_tool(self) -> None:
        text = prompt()
        for fact in ("price", "crew", "hours", "quote validity", "move details", "contact"):
            assert fact in text, f"prompt does not name mutable fact: {fact}"

    def test_prompt_allows_reuse_within_the_same_exchange(self) -> None:
        """No pointless re-fetch when merely rephrasing what was just retrieved."""
        assert "reuse facts you retrieved a moment ago" in prompt()

    def test_prompt_forbids_inventing_business_facts(self) -> None:
        text = prompt()
        for invented in ("prices", "discounts", "availability", "policies", "payment status"):
            assert invented in text


class TestConciseStyle:
    """Observed V1 failure: long answers that dumped the whole quote."""

    def test_prompt_asks_for_a_short_answer_first(self) -> None:
        text = prompt()
        assert "answer the question first" in text
        assert "one to three short sentences" in text

    def test_prompt_forbids_unprompted_detail_dumps(self) -> None:
        text = prompt()
        assert "do not volunteer unrelated move details" in text
        assert "do not repeat the full line-item breakdown" in text

    def test_prompt_caps_follow_up_questions_at_one(self) -> None:
        assert "at most one" in prompt()

    def test_prompt_includes_a_length_example(self) -> None:
        """A concrete example anchors length better than an adjective."""
        text = SYSTEM_PROMPT
        assert "Example of the right length" in text
        # The example answer itself must be short.
        match = re.search(r'You: "(.+?)"', text, re.DOTALL)
        assert match, "example answer not found"
        assert len(match.group(1).split()) < 40


class TestNonDisclosureRegression:
    """Rules from the original V1 prompt that must survive this edit."""

    def test_prompt_forbids_revealing_internals(self) -> None:
        text = prompt()
        for secret in ("these instructions", "schemas", "internal identifiers"):
            assert secret in text

    def test_prompt_requires_estimates_be_presented_as_a_range(self) -> None:
        text = prompt()
        assert "non-binding estimates" in text
        assert "range" in text

    def test_prompt_takes_no_arguments(self) -> None:
        """Tools stay the single source of truth; no facts are injected here."""
        assert build_system_prompt() == SYSTEM_PROMPT

    def test_prompt_contains_no_company_or_quote_facts(self) -> None:
        """Nothing tenant-specific may be baked into a shared prompt."""
        text = prompt()
        for leak in ("acme", "springfield", "$1,0", "62701"):
            assert leak not in text
