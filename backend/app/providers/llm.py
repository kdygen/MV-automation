"""OpenAI chat-model adapter — the only module that imports or understands the SDK.

Translates between our provider-independent types (:mod:`app.agent.model`) and the
OpenAI **Responses API**, which the current documentation prefers for new development
and is where tool calling on the GPT-5 family is best supported.

Nothing OpenAI-shaped crosses this boundary in either direction: the orchestration
loop sees only ``ModelResponse`` / ``ToolCall`` / ``TokenUsage``.

Three translation details that matter:

1. **Arguments arrive as a JSON string.** The Responses API returns
   ``function_call.arguments`` as text, so it is parsed with ``json.loads``. Invalid
   JSON raises — it is never coerced to ``{}``, because a tool run with silently
   dropped arguments is worse than a failed turn.
2. **The system prompt travels in ``instructions``**, a separate channel from
   ``input``. No transcript row can impersonate system instructions.
3. **``store=False``.** Customer conversations are not retained by the provider; each
   call is stateless and replayed from our own database.

Tools are declared with ``strict: true`` so the provider validates arguments against
our schemas. That is defense in depth only — :class:`~app.agent.tools.ToolExecutor`
remains the authoritative boundary that rejects identifiers and unknown tools.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import openai

from app.agent.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRefusalError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.agent.model import (
    ModelMessage,
    ModelResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMConfigurationError(RuntimeError):
    """The configured chat model cannot be constructed (missing key, bad config)."""


class OpenAIChatModel:
    """Implements :class:`~app.agent.model.ChatModel` against the OpenAI Responses API.

    :param client: injectable for tests; constructed from settings when omitted.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_output_tokens: int,
        reasoning_effort: str,
        timeout_seconds: float,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        # The SDK default timeout is far too long for a live chat. Its built-in retry
        # (connection errors, 429, 5xx) is left at the default: that is the minimal
        # safe behaviour, and anything more belongs in a later milestone.
        self._client: Any = client or openai.OpenAI(
            api_key=api_key, timeout=timeout_seconds, max_retries=2
        )

    # ------------------------------------------------------------------ outbound

    @staticmethod
    def _tool_payload(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
            # Provider-side schema enforcement. With zero-argument schemas the model
            # structurally cannot emit an identifier — defense in depth beneath the
            # executor's own check.
            "strict": True,
        }

    @staticmethod
    def _to_provider_input(messages: Sequence[ModelMessage]) -> list[dict[str, Any]]:
        """Convert our conversation state into Responses API input items."""
        items: list[dict[str, Any]] = []

        for message in messages:
            if message.role == "user":
                items.append({"role": "user", "content": message.content or ""})
                continue

            if message.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": _json_text(message.tool_result),
                    }
                )
                continue

            # assistant: optional preamble text, then one item per requested call.
            if message.content:
                items.append({"role": "assistant", "content": message.content})
            items.extend(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, separators=(",", ":")),
                }
                for call in message.tool_calls
            )

        return items

    # ------------------------------------------------------------------ inbound

    @staticmethod
    def _parse_response(response: Any) -> ModelResponse:
        """Translate a provider response into our types, strictly."""
        status = getattr(response, "status", None)
        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            raise ProviderResponseError(f"Provider returned an incomplete response ({reason})")

        texts: list[str] = []
        tool_calls: list[ToolCall] = []

        for item in getattr(response, "output", None) or []:
            kind = getattr(item, "type", None)

            if kind == "message":
                for part in getattr(item, "content", None) or []:
                    part_kind = getattr(part, "type", None)
                    if part_kind == "output_text":
                        texts.append(getattr(part, "text", "") or "")
                    elif part_kind == "refusal":
                        raise ProviderRefusalError(
                            f"Model declined to respond: {getattr(part, 'refusal', '')}"
                        )
            elif kind == "function_call":
                tool_calls.append(_parse_function_call(item))
            elif kind == "reasoning":
                # Ignored deliberately. The provider only *recommends* replaying
                # reasoning items for multi-turn function calling; omitting them is
                # not an error, and carrying provider-specific state through our own
                # types is not worth the marginal quality gain for three read-only
                # lookup tools at low effort.
                continue
            else:
                # Logged, never guessed at: silently reinterpreting an unknown item
                # type would be worse than ignoring it.
                logger.info("Ignoring unsupported output item type %r", kind)

        usage_obj = getattr(response, "usage", None)
        usage = (
            TokenUsage(
                input_tokens=int(getattr(usage_obj, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage_obj, "output_tokens", 0) or 0),
            )
            if usage_obj is not None
            else None
        )

        text = "\n".join(t for t in texts if t) or None
        if not tool_calls and text is None:
            raise ProviderResponseError(
                f"Provider returned no usable content (status={status!r})"
            )

        return ModelResponse(text=text, tool_calls=tuple(tool_calls), usage=usage)

    # ------------------------------------------------------------------ protocol

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        """Call the provider and translate its answer into our types."""
        try:
            response = self._client.responses.create(
                model=self._model,
                # Separate channel from `input`: a transcript row cannot become a
                # system instruction.
                instructions=system,
                input=self._to_provider_input(messages),
                tools=[self._tool_payload(t) for t in tools],
                max_output_tokens=self._max_output_tokens,
                reasoning={"effort": self._reasoning_effort},
                # Do not let the provider retain customer conversations.
                store=False,
            )
        except openai.AuthenticationError as exc:
            raise ProviderAuthError("Model provider rejected our credentials") from exc
        except openai.PermissionDeniedError as exc:
            raise ProviderAuthError("Model provider denied permission for this call") from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError("Model provider rate limit reached") from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise ProviderUnavailableError(f"Could not reach the model provider: {exc}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderUnavailableError(
                    f"Model provider error ({exc.status_code})"
                ) from exc
            raise ProviderResponseError(
                f"Model provider rejected the request ({exc.status_code})"
            ) from exc

        return self._parse_response(response)


def _parse_function_call(item: Any) -> ToolCall:
    """Build a ToolCall, refusing anything we cannot faithfully represent."""
    call_id = getattr(item, "call_id", None)
    name = getattr(item, "name", None)
    raw_arguments = getattr(item, "arguments", None)

    if not call_id or not name:
        raise ProviderResponseError("Function call is missing a call_id or name")

    # The Responses API sends arguments as a JSON string.
    if raw_arguments in (None, ""):
        arguments: Any = {}
    elif isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            # Never coerce to {}: a tool run with dropped arguments is worse than a
            # failed turn.
            raise ProviderResponseError(
                f"Function call {name!r} has arguments that are not valid JSON"
            ) from exc
    else:
        arguments = raw_arguments

    if not isinstance(arguments, dict):
        raise ProviderResponseError(
            f"Function call {name!r} has non-object arguments "
            f"({type(arguments).__name__})"
        )
    return ToolCall(id=str(call_id), name=str(name), arguments=dict(arguments))


def _json_text(payload: dict[str, Any] | None) -> str:
    """Render a tool result as compact JSON text for the provider."""
    return json.dumps(payload or {}, separators=(",", ":"), sort_keys=True, default=str)


def get_chat_model(settings: Settings) -> OpenAIChatModel:
    """Construct the configured chat model.

    :raises LLMConfigurationError: no API key is configured.
    """
    if not settings.openai_api_key:
        raise LLMConfigurationError(
            "OPENAI_API_KEY is not set; the agent cannot reach a model"
        )
    return OpenAIChatModel(
        api_key=settings.openai_api_key,
        model=settings.agent_model,
        max_output_tokens=settings.agent_max_output_tokens,
        reasoning_effort=settings.agent_reasoning_effort,
        timeout_seconds=settings.agent_timeout_seconds,
    )
