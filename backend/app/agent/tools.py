"""Read-only agent tools and the dispatcher that injects their scope.

The model may choose **which** of these tools runs. It may never choose **whose** data
they read: every handler receives an :class:`~app.agent.context.AgentContext` built
server-side, and every query filters on ``context.company_id``.

In Step 1B all three tools take zero model-visible arguments, so there is nowhere for a
hallucinated identifier to go. :data:`FORBIDDEN_ARGUMENTS` is nonetheless enforced by
the executor, because it must keep holding when later tools (e.g. a pricing preview)
legitimately accept business arguments.

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

from app.agent.context import AgentContext
from app.agent.errors import ToolArgumentError, ToolExecutionError, UnknownToolError
from app.agent.model import ToolDefinition
from app.agent.schemas import (
    CompanyInfo,
    MoveDetails,
    QuoteLineItem,
    QuoteSummary,
    SideAccessInfo,
)
from app.core.logging import get_logger
from app.models import Company, MovingRequest, Quote

logger = get_logger(__name__)

DEFAULT_QUOTE_VALIDITY_DAYS = 14

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


def get_quote_summary(db: Session, context: AgentContext) -> QuoteSummary:
    """Return the customer-facing summary of the quote bound to this conversation."""
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


def get_move_details(db: Session, context: AgentContext) -> MoveDetails:
    """Return the move this conversation's quote was priced from."""
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


def get_company_info(db: Session, context: AgentContext) -> CompanyInfo:
    """Return public-facing details of the company this conversation belongs to."""
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


#: name → (model-visible JSON Schema, handler). The registry is the allowlist: there is
#: no dynamic lookup by string anywhere, so the model can only reach these functions.
TOOL_REGISTRY: dict[str, tuple[dict[str, Any], Callable[[Session, AgentContext], Any]]] = {
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
        self._validate_arguments(tool_name, schema, arguments or {})

        result = handler(self._db, self._context)
        return dict(result.model_dump(mode="json"))

    @staticmethod
    def _validate_arguments(
        tool_name: str, schema: dict[str, Any], arguments: dict[str, Any]
    ) -> None:
        """Reject identifiers first, then anything outside the declared schema."""
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

        allowed = set(schema["input_schema"].get("properties", {}))
        unexpected = sorted(supplied - allowed)
        if unexpected:
            raise ToolArgumentError(
                f"{tool_name} does not accept argument(s): {', '.join(unexpected)}"
            )
