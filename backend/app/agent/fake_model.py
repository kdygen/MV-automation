"""Deterministic scripted model for tests and offline development.

Simulates the tool-use **protocol**, never intelligence: it does not read the user's
text or branch on content. A test hands it the exact sequence of responses to return,
then asserts on what the loop did — which is what makes orchestration provable without
a network, an API key, or a nondeterministic model.

Mirrors the house pattern of ``FakeEmailProvider`` / ``FakeDistanceProvider``: the fake
lives in the application package so local development can also run the loop with no
credentials.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.agent.model import (
    ModelMessage,
    ModelResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


@dataclass(frozen=True)
class RecordedCall:
    """What the loop sent to the model on one invocation."""

    system: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...]


class ScriptedModelExhausted(RuntimeError):
    """The loop asked for more responses than the script provides.

    Raised loudly rather than falling back to a default: a silent fallback would let a
    test pass while exercising something other than what it claims.
    """


@dataclass
class ScriptedModel:
    """Returns pre-built responses in order.

    :param script: responses to return, one per ``complete()`` call.
    :param repeat_last: when true, keep returning the final response forever — used to
        drive the loop-limit test with a model that never stops calling tools.
    """

    script: Sequence[ModelResponse]
    repeat_last: bool = False
    calls: list[RecordedCall] = field(default_factory=list)
    _cursor: int = 0

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        self.calls.append(
            RecordedCall(system=system, messages=tuple(messages), tools=tuple(tools))
        )
        if self._cursor < len(self.script):
            response = self.script[self._cursor]
            self._cursor += 1
            return response
        if self.repeat_last and self.script:
            return self.script[-1]
        raise ScriptedModelExhausted(
            f"ScriptedModel ran out of responses after {len(self.script)} call(s)"
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)


# --- Convenience builders, so tests read as intent rather than construction ---

_ids = itertools.count(1)


def text_response(text: str, *, tokens_in: int = 0, tokens_out: int = 0) -> ModelResponse:
    """A final answer."""
    return ModelResponse(
        text=text, usage=TokenUsage(input_tokens=tokens_in, output_tokens=tokens_out)
    )


def tool_response(
    name: str,
    arguments: dict | None = None,
    *,
    call_id: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> ModelResponse:
    """A request to run one tool."""
    return ModelResponse(
        tool_calls=(
            ToolCall(
                id=call_id or f"call_{next(_ids)}",
                name=name,
                arguments=arguments or {},
            ),
        ),
        usage=TokenUsage(input_tokens=tokens_in, output_tokens=tokens_out),
    )


def malformed_response() -> ModelResponse:
    """Neither text nor tool calls — the loop must reject this."""
    return ModelResponse()
