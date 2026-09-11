"""Customer-initiated quote changes: preview deterministically, then persist a revision.

Two guarantees hold everything together:

**Nothing is written until the customer confirms.** ``preview_changes`` builds a
transient :class:`~app.models.MovingRequest` that is never added to the session, prices
it, and returns the comparison. A customer who closes the tab leaves no trace.

**Confirming appends, it never overwrites.** ``confirm_changes`` creates the next
moving request and the next quote, then points the old quote forward. Every revision
keeps its own ``inputs_snapshot``, ``engine_version`` and ``pricing_config_id``, so any
price the customer was ever shown can still be replayed. The customer's original link
keeps working because :func:`resolve_head` follows the chain.

No language model participates in any of this. Availability comes from the availability
service, price comes from ``RuleBasedEngine``, and both are re-checked inside the write
transaction — a preview is a courtesy to the customer, never an input to the decision.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from datetime import date as date_type

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models import (
    ChangeKind,
    Company,
    MovingRequest,
    Quote,
    QuoteChangeRequest,
    QuoteStatus,
)
from app.models.moving_request import HomeSize, PackingService
from app.schemas.requote import ChangePreviewOut, EditMoveIn, MoveDetailsOut, QuoteSideOut
from app.services import availability as availability_service
from app.services import pricing as pricing_service
from app.services import quotes as quote_service

logger = get_logger(__name__)

#: Only a live offer can be edited. An accepted quote has a booking behind it, a
#: declined or expired one is closed, and a superseded one is not the head.
EDITABLE_STATUSES = frozenset({QuoteStatus.SENT})

#: Defence against a corrupted chain: a lookup walks at most this many links before
#: giving up rather than looping forever.
MAX_CHAIN_DEPTH = 50

#: Fields copied verbatim when an edit clones a moving request. Listed explicitly so a
#: column added later must be considered rather than silently dropped or carried.
_COPIED_REQUEST_FIELDS = (
    "company_id",
    "lead_id",
    "origin_line1",
    "origin_city",
    "origin_state",
    "origin_zip",
    "origin_floor",
    "origin_has_elevator",
    "origin_stairs_flights",
    "destination_line1",
    "destination_city",
    "destination_state",
    "destination_zip",
    "destination_floor",
    "destination_has_elevator",
    "destination_stairs_flights",
    "move_date",
    "is_date_flexible",
    "home_size",
    "packing_service",
    "special_items",
    "notes",
    "distance_miles",
    "extracted_by",
)


class RequestChanges(BaseModel):
    """What the customer asked to change. Unset fields are left as they were.

    Field names match :class:`~app.models.MovingRequest` columns exactly, so applying a
    change is a plain override of the cloned request — no mapping table to drift.
    """

    move_date: date_type | None = None
    is_date_flexible: bool | None = None
    home_size: HomeSize | None = None
    packing_service: PackingService | None = None
    special_items: list[str] | None = None
    origin_floor: int | None = None
    origin_has_elevator: bool | None = None
    origin_stairs_flights: int | None = None
    destination_floor: int | None = None
    destination_has_elevator: bool | None = None
    destination_stairs_flights: int | None = None

    @classmethod
    def from_edit(cls, payload: EditMoveIn) -> RequestChanges:
        """Flatten the nested edit payload onto moving-request column names."""
        flat: dict[str, object] = payload.model_dump(
            exclude_none=True, exclude={"origin", "destination"}
        )
        for side in ("origin", "destination"):
            access = getattr(payload, side)
            if access is None:
                continue
            for field, value in access.model_dump(exclude_none=True).items():
                flat[f"{side}_{field}"] = value
        return cls(**flat)

    def applied(self) -> dict[str, object]:
        """Only the fields actually supplied, ready to override a cloned request."""
        return self.model_dump(exclude_none=True)


def resolve_head(db: Session, quote: Quote) -> Quote:
    """Follow the revision chain to the current quote.

    A token stays on the revision it was issued for, so an emailed link keeps working
    after the customer edits their move — it simply resolves forward.
    """
    seen: set[uuid.UUID] = set()
    current = quote
    for _ in range(MAX_CHAIN_DEPTH):
        if current.superseded_by_quote_id is None:
            return current
        if current.id in seen:  # pragma: no cover - only reachable via data corruption
            logger.error("Quote chain cycle detected at %s", current.id)
            return current
        seen.add(current.id)
        nxt = db.get(Quote, current.superseded_by_quote_id)
        if nxt is None:  # pragma: no cover - FK is ON DELETE SET NULL
            return current
        current = nxt
    logger.error("Quote chain from %s exceeded %d links", quote.id, MAX_CHAIN_DEPTH)
    return current


def _company(db: Session, quote: Quote) -> Company:
    company = db.get(Company, quote.company_id)
    if company is None:  # pragma: no cover - FK guarantees it
        raise NotFoundError("Company not found")
    return company


def _request(db: Session, quote: Quote) -> MovingRequest:
    request = db.get(MovingRequest, quote.moving_request_id)
    if request is None:  # pragma: no cover - FK guarantees it
        raise NotFoundError("Move details not found")
    return request


def assert_editable(db: Session, quote: Quote, company: Company) -> None:
    """Raise unless this quote may still be changed by the customer.

    :raises app.core.errors.ConflictError: the quote is closed, already booked, or the
        company reviews every price by hand.
    """
    if quote.status is QuoteStatus.ACCEPTED:
        raise ConflictError(
            "This move is already booked — please contact the company to change it"
        )
    if quote.status is not QuoteStatus.SENT:
        raise ConflictError(f"This quote can no longer be changed (status: {quote.status.value})")

    # Review-mode companies price by hand, so a self-service edit would either bypass
    # their review or strand the customer on a draft they cannot see. Blocking is the
    # honest option until the dashboard can review a customer-requested revision.
    if quote_service.company_review_mode(company):
        raise ConflictError(
            "Changes to this quote need to be confirmed by the company — please contact them"
        )


def _clone_request(request: MovingRequest, changes: dict[str, object]) -> MovingRequest:
    """A new moving request: the old one's values, with the customer's edits applied."""
    fields = {name: getattr(request, name) for name in _COPIED_REQUEST_FIELDS}
    fields.update(changes)
    return MovingRequest(
        **fields,
        supersedes_request_id=request.id,
        # The raw payload records the edit itself, not a fabricated form submission.
        raw_payload={"supersedes": str(request.id), "changes": _jsonable(changes)},
    )


def _jsonable(changes: dict[str, object]) -> dict[str, object]:
    return {
        key: value.isoformat() if isinstance(value, date_type | datetime) else value
        for key, value in changes.items()
    }


def _side(request: MovingRequest, quote: Quote) -> QuoteSideOut:
    return QuoteSideOut(
        move_date=request.move_date,
        currency=quote.currency,
        amount_min_cents=quote.amount_min_cents,
        amount_max_cents=quote.amount_max_cents,
        estimated_hours=quote.estimated_hours,
        crew_size=quote.crew_size,
        line_items=list(quote.line_items),
    )


def _validate(
    db: Session,
    quote: Quote,
    company: Company,
    request: MovingRequest,
    changes: RequestChanges,
    *,
    today: date_type,
) -> dict[str, object]:
    """Run every rule that must hold for this edit, and return the applied changes.

    Called by preview *and* by confirm. Confirm never trusts that a preview happened or
    what it concluded: a date can fill up between the customer seeing it and clicking.
    """
    assert_editable(db, quote, company)

    # A field resubmitted with its current value is not a change. Dropping it here
    # means an edit form that posts every field still produces an honest audit row,
    # and "nothing actually changed" is caught rather than creating a pointless
    # revision with an identical price.
    supplied = changes.applied()
    applied = {
        field: value
        for field, value in supplied.items()
        if not _same_as_current(request, field, value)
    }
    if not applied:
        # Name the specific field when it is the only thing they sent; an edit form
        # posts every field, so a generic message is right for the rest.
        if set(supplied) == {"move_date"}:
            raise ValidationError("That is already your move date")
        raise ValidationError("Nothing was changed")

    new_date = applied.get("move_date")
    if new_date is not None:
        assert isinstance(new_date, date_type)
        availability_service.assert_available(db, company, new_date, today=today)
    return applied


def _same_as_current(request: MovingRequest, field: str, value: object) -> bool:
    """Whether ``value`` already equals what the request holds.

    Enums and lists need normalizing first: the payload carries ``HomeSize.TWO_BR``
    against a column holding the same enum, and ``["piano"]`` against a JSON list.
    """
    current = getattr(request, field)
    if isinstance(current, HomeSize | PackingService) or isinstance(
        value, HomeSize | PackingService
    ):
        return str(current) == str(value)
    if isinstance(value, list) or isinstance(current, list):
        as_list = lambda item: list(item) if isinstance(item, list) else []  # noqa: E731
        return as_list(current) == as_list(value)
    return bool(current == value)


def move_details(request: MovingRequest) -> MoveDetailsOut:
    """The priced inputs, as the customer's edit form loads them."""
    return MoveDetailsOut(
        move_date=request.move_date,
        is_date_flexible=request.is_date_flexible,
        home_size=request.home_size.value,
        packing_service=request.packing_service.value,
        special_items=[str(item) for item in request.special_items],
        origin_city=request.origin_city,
        origin_floor=request.origin_floor,
        origin_has_elevator=request.origin_has_elevator,
        origin_stairs_flights=request.origin_stairs_flights,
        destination_city=request.destination_city,
        destination_floor=request.destination_floor,
        destination_has_elevator=request.destination_has_elevator,
        destination_stairs_flights=request.destination_stairs_flights,
    )


def preview_changes(
    db: Session, quote: Quote, changes: RequestChanges, *, today: date_type | None = None
) -> ChangePreviewOut:
    """Price the requested change without writing anything."""
    today = today or date_type.today()
    company = _company(db, quote)
    request = _request(db, quote)
    applied = _validate(db, quote, company, request, changes, today=today)

    # Transient: never added to the session, so nothing here can be flushed to storage.
    proposed_request = _clone_request(request, applied)
    estimate, _ = pricing_service.estimate_for_request(db, proposed_request)

    validity = timedelta(days=quote_service.company_validity_days(company))
    return ChangePreviewOut(
        current=_side(request, quote),
        proposed=QuoteSideOut(
            move_date=proposed_request.move_date,
            currency=estimate.currency,
            amount_min_cents=estimate.amount_min_cents,
            amount_max_cents=estimate.amount_max_cents,
            estimated_hours=estimate.estimated_hours,
            crew_size=estimate.crew_size,
            line_items=[item.model_dump(mode="json") for item in estimate.line_items],
        ),
        price_changed=(
            estimate.amount_min_cents != quote.amount_min_cents
            or estimate.amount_max_cents != quote.amount_max_cents
        ),
        difference_min_cents=estimate.amount_min_cents - quote.amount_min_cents,
        difference_max_cents=estimate.amount_max_cents - quote.amount_max_cents,
        proposed_valid_until=datetime.now(UTC) + validity,
    )


def confirm_changes(
    db: Session,
    quote: Quote,
    changes: RequestChanges,
    *,
    kind: ChangeKind,
    today: date_type | None = None,
    source: str = "quote_page",
) -> Quote:
    """Persist the requested change as the next revision, and return it.

    Every rule is re-checked here from scratch. The company row is locked first so two
    customers cannot both take the last slot on a date; on SQLite the lock is a no-op,
    which is acceptable because concurrency there is a test-only concern.
    """
    today = today or date_type.today()
    # Serializes capacity-consuming writes for this tenant on PostgreSQL.
    db.execute(select(Company.id).where(Company.id == quote.company_id).with_for_update())

    company = _company(db, quote)
    request = _request(db, quote)
    applied = _validate(db, quote, company, request, changes, today=today)

    new_request = _clone_request(request, applied)
    db.add(new_request)
    db.flush()

    estimate, config_row = pricing_service.estimate_for_request(db, new_request)
    new_quote = Quote(
        company_id=quote.company_id,
        moving_request_id=new_request.id,
        pricing_config_id=config_row.id,
        status=QuoteStatus.SENT,
        currency=estimate.currency,
        amount_min_cents=estimate.amount_min_cents,
        amount_max_cents=estimate.amount_max_cents,
        total_cents=estimate.total_cents,
        estimated_hours=estimate.estimated_hours,
        crew_size=estimate.crew_size,
        engine_version=estimate.engine_version,
        line_items=[item.model_dump(mode="json") for item in estimate.line_items],
        inputs_snapshot=estimate.inputs,
        revision=quote.revision + 1,
        # Its own token, so a confirmation email can link straight to this revision.
        public_token=secrets.token_urlsafe(24),
        valid_until=datetime.now(UTC)
        + timedelta(days=quote_service.company_validity_days(company)),
    )
    db.add(new_quote)
    db.flush()

    # The old row is closed, not rewritten: its price stays replayable forever.
    quote.status = QuoteStatus.SUPERSEDED
    quote.superseded_by_quote_id = new_quote.id

    db.add(
        QuoteChangeRequest(
            company_id=quote.company_id,
            previous_quote_id=quote.id,
            resulting_quote_id=new_quote.id,
            kind=kind,
            requested_changes=_jsonable(applied),
            confirmed_at=datetime.now(UTC),
            source=source,
        )
    )
    db.commit()

    logger.info(
        "Quote %s superseded by %s (r%d, %s change) for company %s",
        quote.id,
        new_quote.id,
        new_quote.revision,
        kind.value,
        quote.company_id,
    )
    return new_quote
