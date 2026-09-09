"""Provider-independent chat-model types.

The orchestration loop speaks only these types, never a vendor SDK's. Providers
disagree structurally — one returns tool calls as typed content blocks, another as
items whose arguments are a JSON *string* — so each is translated in a thin adapter
and switching providers never touches the loop. It also means the loop is exercised
with no network and no API key at all.

Response contract (enforced by the loop):

- ``tool_calls`` non-empty  → the model wants tools run; any ``text`` is preamble
- ``tool_calls`` empty and ``text`` set → the final answer
- neither → malformed, and the loop raises ``ModelProtocolError``

Deliberately absent: a ``system`` message role (the system prompt is a separate
argument to :meth:`ChatModel.complete`, so no transcript row can ever impersonate
system instructions), sampling parameters, model names, streaming, multimodal content
and raw provider payloads — all provider concerns, not orchestration concerns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

MessageRoleLiteral = Literal["user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    """A model's request to run one allowlisted tool.

    :param id: provider correlation id, echoed back with the result so the model can
        pair them. Synthesized deterministically when replaying history.
    :param arguments: business arguments only — identity is injected server-side and
        any identifier here is refused by the executor.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDefinition:
    """A tool as advertised to the model (provider-neutral JSON Schema)."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class TokenUsage:
    """Usage for one model call. Optional: not every provider or fake reports it."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class ModelMessage:
    """One entry of model-visible conversation state.

    Carries no database ids, timestamps, or ORM objects — only what a model needs.
    """

    role: MessageRoleLiteral
    content: str | None = None
    # Set on an assistant message that is requesting tools.
    tool_calls: tuple[ToolCall, ...] = ()
    # Set on a tool message answering a specific request.
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelResponse:
    """One completion from a model."""

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_final(self) -> bool:
        return not self.tool_calls and self.text is not None


class ChatModel(Protocol):
    """Everything the orchestration loop needs from a language model."""

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        """Produce the next response given conversation state and available tools."""
        ...
