"""Agent orchestration: run one customer turn to a final answer.

Sequence per turn: persist the customer's message, replay the tenant-scoped
transcript, then alternate between the model and the tool executor until the model
produces final text or the iteration budget runs out.

Two invariants this module exists to hold:

1. **Tools are only ever reached through** :class:`~app.agent.tools.ToolExecutor`.
   No handler is imported here, so the 1B allowlist, forbidden-identifier check and
   server-side scope injection cannot be bypassed.
2. **Boundary violations are not retried.** An unknown tool or a smuggled identifier
   propagates immediately; the model is never invited to "try again" after attempting
   to widen its own scope.

No transaction is held across a model call: each row commits on its own, so a slow
provider never pins a database connection. The cost is that a failed turn leaves the
user message and any completed tool rows behind — which is the correct audit trail of
what actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent.actions import UiAction
from app.agent.context import AgentContext
from app.agent.errors import AgentLoopError, ModelProtocolError
from app.agent.model import ChatModel, ModelMessage, TokenUsage, ToolCall
from app.agent.tools import TOOL_DEFINITIONS, ToolExecutor
from app.core.logging import get_logger
from app.models import Message, MessageRole
from app.services import agent as agent_service

logger = get_logger(__name__)

#: Model/tool round trips allowed in one turn. Enough for a realistic
#: quote → move → company chain plus slack; small enough that a looping model costs
#: little and a malformed provider response cannot spin.
DEFAULT_MAX_ITERATIONS = 6


@dataclass(frozen=True)
class AgentTurnResult:
    """Outcome of one completed turn."""

    reply: str
    tools_used: tuple[str, ...]
    iterations: int
    usage: TokenUsage
    #: A navigation hint for the frontend, or ``NONE``. Read off the executor rather
    #: than matched here, so this module still names no tool.
    ui_action: UiAction = UiAction.NONE


def _synthesized_call_id(message: Message) -> str:
    """Deterministic id pairing a replayed tool result with its request.

    Providers reject a tool result that does not follow a matching tool-use block, but
    we deliberately do not persist the assistant's request as its own row. Deriving the
    id from the tool row's primary key reconstructs a valid pair on replay without a
    second row or a fourth message role.
    """
    return f"call_{message.id.hex[:16]}"


def to_model_messages(messages: list[Message]) -> list[ModelMessage]:
    """Convert a persisted transcript into model-visible conversation state.

    Emits no database ids, timestamps, token counts, or ORM objects. A persisted tool
    row expands into **two** model messages — a synthesized assistant request followed
    by its result — so the replayed conversation is protocol-valid.

    Tool results are replayed straight from ``tool_result``, which is already the
    allowlisted structure produced in Step 1B: safe by construction rather than by
    re-sanitizing here.
    """
    converted: list[ModelMessage] = []
    for message in messages:
        if message.role is MessageRole.USER:
            converted.append(ModelMessage(role="user", content=message.content))
        elif message.role is MessageRole.ASSISTANT:
            converted.append(ModelMessage(role="assistant", content=message.content))
        elif message.role is MessageRole.TOOL:
            call_id = _synthesized_call_id(message)
            converted.append(
                ModelMessage(
                    role="assistant",
                    tool_calls=(
                        ToolCall(
                            id=call_id,
                            name=message.tool_name or "",
                            arguments=dict(message.tool_args or {}),
                        ),
                    ),
                )
            )
            converted.append(
                ModelMessage(
                    role="tool",
                    tool_call_id=call_id,
                    tool_name=message.tool_name,
                    tool_result=dict(message.tool_result or {}),
                )
            )
    return converted


def _persist(db: Session, message: Message) -> Message:
    db.add(message)
    db.commit()
    return message


def run_agent_turn(
    db: Session,
    context: AgentContext,
    user_message: str,
    model: ChatModel,
    *,
    system_prompt: str = "",
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> AgentTurnResult:
    """Run one customer turn to completion.

    :param context: trusted scope; the model never influences it.
    :param system_prompt: supplied by the caller — prompt authoring is a later step.
    :raises ValueError: the customer message is blank.
    :raises AgentLoopError: the turn did not converge within ``max_iterations``.
    :raises ModelProtocolError: the model returned neither tools nor text.
    :raises app.agent.errors.UnknownToolError: boundary violation — not retried.
    :raises app.agent.errors.ToolArgumentError: boundary violation — not retried.
    :raises app.agent.errors.ToolExecutionError: expected data was missing.
    """
    text = user_message.strip()
    if not text:
        raise ValueError("user_message must not be blank")

    _persist(
        db,
        Message(
            conversation_id=context.conversation_id,
            role=MessageRole.USER,
            content=text,
        ),
    )

    # Replay through the centralized tenant-safe helper — never an ad-hoc query.
    messages = to_model_messages(agent_service.list_transcript(db, context))

    executor = ToolExecutor(db, context)
    usage = TokenUsage()
    tools_used: list[str] = []

    for iteration in range(1, max_iterations + 1):
        response = model.complete(
            system=system_prompt, messages=messages, tools=TOOL_DEFINITIONS
        )
        if response.usage is not None:
            usage = usage + response.usage

        if response.wants_tools:
            # Echo the model's own request back into state so each result is paired.
            messages.append(
                ModelMessage(
                    role="assistant",
                    content=response.text,
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                # The only route to a tool. Boundary violations raise straight out.
                result = executor.execute(call.name, call.arguments)
                tools_used.append(call.name)

                _persist(
                    db,
                    Message(
                        conversation_id=context.conversation_id,
                        role=MessageRole.TOOL,
                        tool_name=call.name,
                        tool_args=dict(call.arguments),
                        tool_result=result,
                    ),
                )
                messages.append(
                    ModelMessage(
                        role="tool",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        tool_result=result,
                    )
                )
            continue

        if response.is_final:
            assert response.text is not None  # narrowed by is_final
            _persist(
                db,
                Message(
                    conversation_id=context.conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=response.text,
                    # Turn totals, not just this call: intermediate calls are not
                    # persisted, so this keeps SUM(tokens) per conversation truthful.
                    tokens_in=usage.input_tokens,
                    tokens_out=usage.output_tokens,
                ),
            )
            logger.info(
                "Agent turn complete for conversation %s: %d iteration(s), tools=%s",
                context.conversation_id,
                iteration,
                tools_used or "none",
            )
            return AgentTurnResult(
                reply=response.text,
                tools_used=tuple(tools_used),
                iterations=iteration,
                usage=usage,
                ui_action=executor.suggested_action,
            )

        raise ModelProtocolError(
            "Model response contained neither tool calls nor final text"
        )

    logger.warning(
        "Agent turn hit the %d-iteration limit for conversation %s (tools=%s)",
        max_iterations,
        context.conversation_id,
        tools_used,
    )
    raise AgentLoopError(
        f"Turn did not converge within {max_iterations} iterations"
    )
