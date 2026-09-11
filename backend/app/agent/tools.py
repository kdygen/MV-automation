"""Read-only agent tools and the dispatcher that injects their scope.

The model may choose **which** of these tools runs. It may never choose **whose** data
they read: every handler receives an :class:`~app.agent.context.AgentContext` built
server-side, and every query filters on ``context.company_id``.

``search_company_knowledge`` is the first tool with a model-visible argument. That
argument is a free-text search string, never an identifier: it selects *what* to look
for, while :class:`AgentContext` still decides *whose* knowledge is searched.
:data:`FORBIDDEN_ARGUMENTS` is enforced by the executor before any schema check, so the
widened surface adds no way to steer scope.

Every tool here is strictly read-only — no handler writes, and none calls a service
that writes (notably, ``is_expired`` is computed in memory rather than by invoking the
quote service's lazy-expiry path).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.actions import SELECTABLE_VALUES, UiAction
from app.agent.context import AgentContext
from app.agent.errors import ToolArgumentError, ToolExecutionError, UnknownToolError
from app.agent.model import ToolDefinition
from app.agent.schemas import (
    CompanyInfo,
    KnowledgeEntry,
    KnowledgeResults,
    MoveDetails,
    NextStep,
    QuoteLineItem,
    QuoteSummary,
    SideAccessInfo,
)
from app.core.logging import get_logger
from app.models import Company, MovingRequest, Quote
from app.services.knowledge import MAX_RESULTS, search_knowledge

logger = get_logger(__name__)

DEFAULT_QUOTE_VALIDITY_DAYS = 14

#: Longest search string the knowledge tool accepts. Bounds the work a single model turn
#: can trigger and stops a whole transcript being passed off as a "query".
#:
#: Enforced server-side only, and deliberately absent from the model-visible JSON Schema.
#: OpenAI's strict-mode schema support is a documented *subset* of JSON Schema, and the
#: published list of permitted keywords could not be confirmed to include ``maxLength``;
#: an unsupported keyword is rejected with a 400, which would take the whole chat
#: endpoint down for a constraint the model cannot be trusted to honour anyway. The
#: limit lives where it is actually enforceable.
MAX_KNOWLEDGE_QUERY_LENGTH = 200

#: Argument names a tool may never accept from a model, regardless of its schema.
#: Identity is injected from :class:`AgentContext`; anything resembling a resource
#: reference arriving as a model argument is treated as an attempt to widen scope.
FORBIDDEN_ARGUMENTS = frozenset(
    {
        "company_id",
        "conversation_id",
        "quote_id",
        "lead_id",
        "moving_request_id",
        "pricing_config_id",
        "booking_id",
        "job_id",
        "user_id",
        "public_token",
        "token",
        "id",
        "slug",
        "company_slug",
    }
)

# JSON Schema shared by every zero-argument tool. ``additionalProperties: False`` means
# a model that invents an argument fails validation rather than having it ignored.
_NO_ARGUMENTS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _as_utc(value: datetime) -> datetime:
    """Normalize DB datetimes (SQLite returns them naive) to aware UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _load_quote(db: Session, context: AgentContext) -> Quote:
    """Fetch the bound quote, filtered by tenant.

    The ``company_id`` predicate is what makes a wrong or tampered ``quote_id``
    unusable: it can only ever match a row inside the caller's own tenant.
    """
    quote = db.scalar(
        select(Quote).where(
            Quote.id == context.quote_id, Quote.company_id == context.company_id
        )
    )
    if quote is None:
        raise ToolExecutionError("The quote for this conversation is unavailable")
    return quote


def get_quote_summary(
    db: Session, context: AgentContext, arguments: dict[str, Any]
) -> QuoteSummary:
    """Return the customer-facing summary of the quote bound to this conversation.

    Takes no arguments; ``arguments`` is present only so every handler shares one
    signature, and is empty by the time the executor calls this.
    """
    quote = _load_quote(db, context)
    return QuoteSummary(
        status=quote.status.value,
        currency=quote.currency,
        amount_min_cents=quote.amount_min_cents,
        amount_max_cents=quote.amount_max_cents,
        estimated_hours=quote.estimated_hours,
        crew_size=quote.crew_size,
        line_items=tuple(
            QuoteLineItem(
                code=str(item.get("code", "")),
                label=str(item.get("label", "")),
                amount_cents=int(item.get("amount_cents", 0)),
            )
            for item in quote.line_items
        ),
        valid_until=quote.valid_until,
        # Computed in memory: tools never write, so no lazy-expiry side effect here.
        is_expired=_as_utc(quote.valid_until) < datetime.now(UTC),
    )


def get_move_details(
    db: Session, context: AgentContext, arguments: dict[str, Any]
) -> MoveDetails:
    """Return the move this conversation's quote was priced from. Takes no arguments."""
    quote = _load_quote(db, context)
    request = db.scalar(
        select(MovingRequest).where(
            MovingRequest.id == quote.moving_request_id,
            MovingRequest.company_id == context.company_id,
        )
    )
    if request is None:
        raise ToolExecutionError("The move details for this quote are unavailable")

    return MoveDetails(
        move_date=request.move_date,
        is_date_flexible=request.is_date_flexible,
        home_size=request.home_size.value,
        packing_service=request.packing_service.value,
        special_items=tuple(str(item) for item in request.special_items),
        distance_miles=request.distance_miles,
        notes=request.notes,
        origin=SideAccessInfo(
            city=request.origin_city,
            state=request.origin_state,
            zip=request.origin_zip,
            floor=request.origin_floor,
            has_elevator=request.origin_has_elevator,
            stairs_flights=request.origin_stairs_flights,
        ),
        destination=SideAccessInfo(
            city=request.destination_city,
            state=request.destination_state,
            zip=request.destination_zip,
            floor=request.destination_floor,
            has_elevator=request.destination_has_elevator,
            stairs_flights=request.destination_stairs_flights,
        ),
    )


def get_company_info(
    db: Session, context: AgentContext, arguments: dict[str, Any]
) -> CompanyInfo:
    """Return public-facing details of the company. Takes no arguments."""
    company = db.scalar(select(Company).where(Company.id == context.company_id))
    if company is None:
        raise ToolExecutionError("Company details are unavailable")

    settings = company.settings or {}
    return CompanyInfo(
        name=company.name,
        phone=company.phone,
        email=company.email,
        # Allowlisted out of the settings JSON; the rest (e.g. quote_review_mode) is
        # internal and must not reach the customer or the model.
        quote_validity_days=int(
            settings.get("quote_validity_days", DEFAULT_QUOTE_VALIDITY_DAYS)
        ),
    )


def search_company_knowledge(
    db: Session, context: AgentContext, arguments: dict[str, Any]
) -> KnowledgeResults:
    """Search this company's published policies and FAQs for the customer's question.

    The only model-supplied input in the tool layer. It is validated here as well as by
    the executor's schema check, because this handler must be safe on its own terms:
    a non-string or oversized query is a client error, not something to pass through.
    """
    raw = arguments.get("query")
    if not isinstance(raw, str):
        raise ToolArgumentError("search_company_knowledge requires a text query")

    query = raw.strip()
    if not query:
        raise ToolArgumentError("search_company_knowledge requires a non-empty query")
    if len(query) > MAX_KNOWLEDGE_QUERY_LENGTH:
        raise ToolArgumentError(
            f"query must be at most {MAX_KNOWLEDGE_QUERY_LENGTH} characters"
        )

    # company_id comes from the server-built context — never from `arguments`.
    matches = search_knowledge(db, context.company_id, query)
    return KnowledgeResults(
        results=tuple(
            # Rebuilt field by field: the score and the curated keywords stay internal.
            KnowledgeEntry(
                category=match.category, title=match.title, content=match.content
            )
            for match in matches
        )
    )


def suggest_next_step(
    db: Session, context: AgentContext, arguments: dict[str, Any]
) -> NextStep:
    """Name the on-screen control the customer should use next.

    The one tool that exists purely to *navigate*. It reads nothing and writes nothing:
    it validates the requested action against the allowlist and hands the name back.
    Every state change still happens through a deterministic endpoint the customer
    triggers themselves, so a model that calls this has changed exactly nothing.
    """
    raw = arguments.get("action")
    if not isinstance(raw, str):
        raise ToolArgumentError("suggest_next_step requires an action")
    try:
        action = UiAction(raw.strip().lower())
    except ValueError:
        raise ToolArgumentError(f"Unknown action: {raw}") from None
    if action.value not in SELECTABLE_VALUES:
        raise ToolArgumentError(f"Action {action.value} cannot be suggested")
    return NextStep(action=action.value)


#: name → (model-visible JSON Schema, handler). The registry is the allowlist: there is
#: no dynamic lookup by string anywhere, so the model can only reach these functions.
TOOL_REGISTRY: dict[
    str, tuple[dict[str, Any], Callable[[Session, AgentContext, dict[str, Any]], Any]]
] = {
    "get_quote_summary": (
        {
            "name": "get_quote_summary",
            "description": (
                "Get the customer's current quote: price range, estimated hours, crew "
                "size, line-item breakdown, and how long it stays valid."
            ),
            "input_schema": _NO_ARGUMENTS,
        },
        get_quote_summary,
    ),
    "get_move_details": (
        {
            "name": "get_move_details",
            "description": (
                "Get the details of the move this quote was priced from: date, home "
                "size, packing service, special items, distance, and access at each "
                "address (floor, elevator, stairs)."
            ),
            "input_schema": _NO_ARGUMENTS,
        },
        get_move_details,
    ),
    "get_company_info": (
        {
            "name": "get_company_info",
            "description": (
                "Get the moving company's contact details and how many days a quote "
                "remains valid."
            ),
            "input_schema": _NO_ARGUMENTS,
        },
        get_company_info,
    ),
    "search_company_knowledge": (
        {
            "name": "search_company_knowledge",
            "description": (
                "Search this moving company's published policies and FAQs — insurance "
                "and certificates of insurance, packing supplies, cancellation and "
                "rescheduling, payment methods, tipping, prohibited or special items, "
                "storage, and similar company-specific rules. Use this whenever the "
                "customer asks what the company does, allows, requires, or charges "
                f"for beyond their quote. Returns up to {MAX_RESULTS} entries, or none "
                "if this company has published nothing on the subject."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The customer's question or the topic to look up, in their "
                            "own words (e.g. 'certificate of insurance for my "
                            "building'). A short phrase, not a whole conversation."
                        ),
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        search_company_knowledge,
    ),
    "suggest_next_step": (
        {
            "name": "suggest_next_step",
            "description": (
                "Show the customer the on-screen control for what they want to do. "
                "Call this when they want to change their move date (change_date), "
                "correct or update move details such as home size, packing, special "
                "items or access (edit_move), go ahead and book (accept_quote), or "
                "reach a person at the company (contact_company). This only reveals a "
                "button — it does not perform the action, so never say the change has "
                "been made. Do not call it for questions you are simply answering."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(SELECTABLE_VALUES),
                        "description": "Which control to reveal.",
                    }
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
        suggest_next_step,
    ),
}

#: The tool definitions a model is shown. Ordering is stable for prompt caching.
TOOL_SCHEMAS: tuple[dict[str, Any], ...] = tuple(
    schema for schema, _ in TOOL_REGISTRY.values()
)

#: The same allowlist in provider-neutral form, for the orchestration loop. Derived
#: from TOOL_SCHEMAS so there is exactly one source of truth for what a model is shown.
TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = tuple(
    ToolDefinition(
        name=schema["name"],
        description=schema["description"],
        input_schema=schema["input_schema"],
    )
    for schema in TOOL_SCHEMAS
)


class ToolExecutor:
    """Dispatches an approved tool call within a fixed, server-owned scope.

    Translation performed here:

        tool name + business arguments  →  AgentContext  →  services/models  →  dict

    The executor is the only place identity enters a tool call, and it never accepts
    identity from its caller's arguments.
    """

    def __init__(self, db: Session, context: AgentContext) -> None:
        self._db = db
        self._context = context
        # Set when a tool returns a navigation hint. Recorded here rather than in the
        # orchestration loop so the loop never has to know a tool's name — the property
        # that keeps the allowlist the single place tools are identified.
        self._suggested_action = UiAction.NONE

    @property
    def suggested_action(self) -> UiAction:
        """The action the model asked to reveal this turn, or ``NONE``.

        Last call wins: a model that changes its mind mid-turn gets the later hint.
        """
        return self._suggested_action

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(TOOL_REGISTRY)

    def execute(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run an allowlisted tool and return a JSON-serializable result.

        :raises UnknownToolError: the name is not on the allowlist.
        :raises ToolArgumentError: an argument was supplied that the tool rejects —
            in particular any resource identifier.
        :raises ToolExecutionError: expected data was missing.
        """
        entry = TOOL_REGISTRY.get(tool_name)
        if entry is None:
            logger.warning("Agent requested unknown tool %r", tool_name)
            raise UnknownToolError(f"Unknown tool: {tool_name}")

        schema, handler = entry
        supplied = arguments or {}
        self._validate_arguments(tool_name, schema, supplied)

        # Handlers receive the validated business arguments and nothing else. Identity
        # travels only in ``self._context``, which no caller of ``execute`` can set.
        result = handler(self._db, self._context, supplied)
        payload = dict(result.model_dump(mode="json"))

        # A tool result carrying an allowlisted action is a navigation hint. Matched on
        # the result's shape, not the tool's name, so the executor stays generic.
        if isinstance(result, NextStep):
            self._suggested_action = UiAction(payload["action"])
        return payload

    @staticmethod
    def _validate_arguments(
        tool_name: str, schema: dict[str, Any], arguments: dict[str, Any]
    ) -> None:
        """Reject identifiers first, then unexpected keys, then missing required ones."""
        supplied = set(arguments)

        # Checked before the schema so the log and the message name the real problem:
        # an attempt to steer scope, not merely an unexpected key.
        smuggled = sorted(supplied & FORBIDDEN_ARGUMENTS)
        if smuggled:
            logger.warning(
                "Agent tried to supply identifier(s) %s to %r — refused",
                smuggled,
                tool_name,
            )
            raise ToolArgumentError(
                f"{tool_name} does not accept {', '.join(smuggled)}; "
                "scope is determined by the server"
            )

        input_schema = schema["input_schema"]
        allowed = set(input_schema.get("properties", {}))
        unexpected = sorted(supplied - allowed)
        if unexpected:
            raise ToolArgumentError(
                f"{tool_name} does not accept argument(s): {', '.join(unexpected)}"
            )

        # Enforced here rather than left to the handler so every tool's required
        # arguments are guaranteed present by the time it runs.
        missing = sorted(set(input_schema.get("required", [])) - supplied)
        if missing:
            raise ToolArgumentError(
                f"{tool_name} requires argument(s): {', '.join(missing)}"
            )
