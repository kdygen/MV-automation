"""Public post-quote chat endpoint.

Wires the existing agent to HTTP without adding logic of its own: the token resolves a
trusted :class:`~app.agent.context.AgentContext` server-side, ``run_agent_turn`` drives
the model and tools, and this module only translates the outcome into a safe HTTP
response.

Two customer-controlled inputs exist, both narrow: the quote token in the URL and the
message text in the body. Everything else — company, quote, conversation — is resolved
by the server. A body carrying an identifier is rejected by
:class:`~app.schemas.chat.ChatMessageIn` (``extra="forbid"``) before any code runs,
and :class:`~app.agent.tools.ToolExecutor` remains the authoritative boundary beneath
that.

Nothing internal reaches the customer: no identifiers, no token echo, no tool names or
results, no provider messages, no prompt. Failures map to generic public codes while
the useful detail goes to the logs.
"""

from __future__ import annotations

import time
from functools import lru_cache

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.agent import (
    AgentLoopError,
    ChatModel,
    ModelProtocolError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRefusalError,
    ProviderResponseError,
    ProviderUnavailableError,
    ToolArgumentError,
    UnknownToolError,
    build_system_prompt,
    run_agent_turn,
)
from app.agent.errors import ToolExecutionError
from app.core.config import Settings, get_settings
from app.core.errors import (
    AppError,
    RateLimitError,
    ServiceUnavailableError,
    UpstreamError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.ratelimit import SlidingWindowLimiter, client_ip, rate_limited
from app.db.session import get_db
from app.providers.llm import LLMConfigurationError, get_chat_model
from app.schemas.chat import ChatMessageIn, ChatReplyOut
from app.services import agent as agent_service

logger = get_logger(__name__)

router = APIRouter(prefix="/public", tags=["chat"])

# Per-IP: stricter than the free quote actions (30/10min) because every call here is a
# billed model request.
_limit_chat_ip = rate_limited("public_chat", max_requests=10, window_seconds=600)

# Per-token limits, applied in addition to per-IP. Per-IP alone fails in both
# directions: customers behind shared NAT/carrier addresses collide with each other,
# while an attacker rotating IPs escapes it entirely. Because each call costs money,
# the per-token limit bounds spend on the real unit of risk — one quote — and cannot be
# evaded by changing address, since an attacker only holds tokens they were given.
_TOKEN_LIMIT_MAX_REQUESTS = 20
_TOKEN_LIMIT_WINDOW_SECONDS = 3600.0


def _enforce_token_limit(request: Request, token: str, settings: Settings) -> None:
    """Apply the per-token chat limit, reusing the app-scoped limiter registry."""
    if not settings.rate_limit_enabled:
        return
    state = request.app.state
    if not hasattr(state, "rate_limiters"):
        state.rate_limiters = {}
    limiters: dict[str, SlidingWindowLimiter] = state.rate_limiters
    limiter = limiters.setdefault(
        "public_chat_token",
        SlidingWindowLimiter(_TOKEN_LIMIT_MAX_REQUESTS, _TOKEN_LIMIT_WINDOW_SECONDS),
    )
    if not limiter.allow(f"public_chat_token:{token}"):
        raise RateLimitError(
            "This conversation has reached its message limit for now — "
            "please try again later."
        )


@lru_cache(maxsize=1)
def _cached_chat_model() -> ChatModel:
    """One model client per process.

    Not per request: constructing an SDK client each time discards its connection pool
    and adds a TLS handshake to every turn. Not at startup: the rest of the API must
    still boot and serve quotes when no key is configured. ``lru_cache`` does not cache
    exceptions, so a missing key retries cleanly instead of poisoning the cache.
    """
    return get_chat_model(get_settings())


def chat_model_dep() -> ChatModel:
    """Resolve the chat model (overridden in tests with a scripted fake)."""
    try:
        return _cached_chat_model()
    except LLMConfigurationError as exc:
        # Misconfiguration, not the customer's fault — and the reason stays internal.
        logger.error("Chat requested but the model is not configured: %s", exc)
        raise ServiceUnavailableError(
            "The assistant is unavailable right now. Please try again later."
        ) from exc


@router.post(
    "/quotes/{token}/chat",
    response_model=ChatReplyOut,
    dependencies=[Depends(_limit_chat_ip)],
)
def chat(
    token: str,
    payload: ChatMessageIn,
    request: Request,
    db: Session = Depends(get_db),
    model: ChatModel = Depends(chat_model_dep),
    settings: Settings = Depends(get_settings),
) -> ChatReplyOut:
    """Send one customer message to the post-quote assistant and return its reply.

    The quote token is the only credential and the only scope: it resolves the quote,
    its conversation, and the tenant, all server-side.
    """
    _enforce_token_limit(request, token, settings)

    # Raises NotFoundError (404) for an unknown token, before any model call.
    context = agent_service.build_agent_context(db, token)

    started = time.perf_counter()
    try:
        result = run_agent_turn(
            db,
            context,
            payload.message,
            model,
            system_prompt=build_system_prompt(),
        )
    except (ToolArgumentError, UnknownToolError) as exc:
        # The model tried to leave its sandbox. Not retried, and never explained to
        # the customer; logged as a security event for someone to look at.
        logger.warning(
            "Agent boundary violation on conversation %s: %s", context.conversation_id, exc
        )
        raise UpstreamError("The assistant could not complete that request.") from exc
    except ProviderRateLimitError as exc:
        logger.warning("Model provider rate limited conversation %s", context.conversation_id)
        raise RateLimitError(
            "The assistant is busy right now — please try again in a moment."
        ) from exc
    except (ProviderAuthError, ProviderUnavailableError) as exc:
        logger.error(
            "Model provider unavailable for conversation %s: %s",
            context.conversation_id,
            exc,
        )
        raise ServiceUnavailableError(
            "The assistant is unavailable right now. Please try again later."
        ) from exc
    except ProviderRefusalError as exc:
        logger.info("Model declined conversation %s: %s", context.conversation_id, exc)
        raise ValidationError("I can't help with that request.") from exc
    except (
        ProviderResponseError,
        ModelProtocolError,
        AgentLoopError,
        ToolExecutionError,
    ) as exc:
        logger.error("Agent turn failed for conversation %s: %s", context.conversation_id, exc)
        raise UpstreamError("The assistant could not complete that request.") from exc
    except AppError:
        # Already a safe, intentional public error (e.g. NotFoundError) — let it pass.
        raise
    except Exception as exc:  # noqa: BLE001 - last line of defence
        # Traceback to the logs; the customer gets nothing implementation-specific.
        logger.exception("Unexpected failure in chat for conversation %s", context.conversation_id)
        raise UpstreamError("The assistant could not complete that request.") from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    # Operational metadata only: no message content, no reply text, no token, no PII.
    logger.info(
        "Chat turn ok conversation=%s ip=%s iterations=%d tools=%s "
        "tokens_in=%d tokens_out=%d latency_ms=%d",
        context.conversation_id,
        client_ip(request),
        result.iterations,
        ",".join(result.tools_used) or "none",
        result.usage.input_tokens,
        result.usage.output_tokens,
        elapsed_ms,
    )
    # The action is a name, not a capability: the frontend decides whether to render
    # it, and the control it opens calls a deterministic endpoint of its own.
    return ChatReplyOut(reply=result.reply, ui_action=result.ui_action.value)
