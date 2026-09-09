"""The agent capability boundary.

This package defines *what a language model is allowed to do* on the server: a frozen
:class:`~app.agent.context.AgentContext` carrying the tenant scope, an explicit
allowlist of read-only tools, and a dispatcher that injects identity rather than
accepting it.

Design rule: the model chooses **which** approved tool runs; it never chooses **whose**
data that tool reads. Scope comes exclusively from ``AgentContext``, which is built
server-side from a quote token.

No LLM client lives here — this package is deliberately usable (and testable) with no
network and no API key.
"""

from app.agent.context import AgentContext
from app.agent.errors import AgentError, ToolArgumentError, ToolExecutionError, UnknownToolError
from app.agent.tools import TOOL_SCHEMAS, ToolExecutor

__all__ = [
    "TOOL_SCHEMAS",
    "AgentContext",
    "AgentError",
    "ToolArgumentError",
    "ToolExecutionError",
    "ToolExecutor",
    "UnknownToolError",
]
