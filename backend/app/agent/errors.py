"""Agent-layer exceptions.

These describe *model misbehaviour* (an unknown tool, a disallowed argument) or a data
problem inside a tool. Scope violations are deliberately **not** represented here —
they raise :class:`~app.core.errors.ForbiddenError` from the core hierarchy, because a
cross-tenant attempt is a security event for the application to reject, never a soft
failure the model could be handed back and retry around.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for agent capability-boundary failures."""


class UnknownToolError(AgentError):
    """The model requested a tool that is not on the allowlist."""


class ToolArgumentError(AgentError):
    """The model supplied an argument the tool does not accept.

    Raised for unknown keys and, explicitly, for any attempt to pass a resource
    identifier — identity comes from :class:`~app.agent.context.AgentContext` only.
    """


class ToolExecutionError(AgentError):
    """A tool could not complete because expected data was missing or malformed."""


class AgentLoopError(AgentError):
    """The turn did not converge within its iteration budget.

    Rows already written (the user message, any tool calls) are deliberately left in
    place: they are the record of what the model attempted.
    """


class ModelProtocolError(AgentError):
    """The model returned a response that satisfies neither branch of the contract.

    A response must either request tools or provide final text. Anything else is a
    provider/adapter fault, not something to retry around.
    """


class ProviderError(AgentError):
    """Base class for failures talking to the model provider."""


class ProviderAuthError(ProviderError):
    """The provider rejected our credentials (missing/invalid/insufficient key)."""


class ProviderRateLimitError(ProviderError):
    """The provider throttled us. The SDK already retried; this is the final answer."""


class ProviderUnavailableError(ProviderError):
    """Timeout, connection failure, or provider-side 5xx."""


class ProviderResponseError(ProviderError):
    """The provider returned something we cannot faithfully translate.

    Includes malformed tool-call arguments. Never coerced into an empty dict — a
    tool invoked with silently-dropped arguments is worse than a failed turn.
    """


class ProviderRefusalError(ProviderError):
    """The model declined to answer (``stop_reason == "refusal"``)."""
