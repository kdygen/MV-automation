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
from app.agent.errors import (
    AgentError,
    AgentLoopError,
    ModelProtocolError,
    ToolArgumentError,
    ToolExecutionError,
    UnknownToolError,
)
from app.agent.loop import AgentTurnResult, run_agent_turn, to_model_messages
from app.agent.model import (
    ChatModel,
    ModelMessage,
    ModelResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from app.agent.tools import TOOL_DEFINITIONS, TOOL_SCHEMAS, ToolExecutor

__all__ = [
    "TOOL_DEFINITIONS",
    "TOOL_SCHEMAS",
    "AgentContext",
    "AgentError",
    "AgentLoopError",
    "AgentTurnResult",
    "ChatModel",
    "ModelMessage",
    "ModelProtocolError",
    "ModelResponse",
    "TokenUsage",
    "ToolArgumentError",
    "ToolCall",
    "ToolDefinition",
    "ToolExecutionError",
    "ToolExecutor",
    "UnknownToolError",
    "run_agent_turn",
    "to_model_messages",
]
