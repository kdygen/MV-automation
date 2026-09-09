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
