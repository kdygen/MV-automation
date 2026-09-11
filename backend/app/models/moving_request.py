"""Moving request model — the structured ``MoveSpec`` persisted for a lead.

This row is the input to the pricing engine: origin/destination (with access
difficulty: floor, elevator, stairs), the move date, home size, packing service, and
special items. ``raw_payload`` preserves exactly what the customer submitted (form
fields today, LLM extraction later) for auditability; ``extracted_by`` records which
path produced the structured data.

Addresses are embedded as prefixed columns rather than a separate table: a request
always has exactly two, they are immutable once submitted, and the pricing engine
consumes them together — normalizing them would buy joins, not correctness. US-format
addresses are assumed for the MVP.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date
from typing import Any

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID, JSONType


class HomeSize(enum.StrEnum):
    STUDIO = "studio"
    ONE_BR = "1br"
    TWO_BR = "2br"
    THREE_BR = "3br"
    FOUR_BR = "4br"
    FIVE_BR_PLUS = "5br_plus"


class PackingService(enum.StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class RequestStatus(enum.StrEnum):
    PENDING = "pending"  # submitted, not yet quoted
    QUOTED = "quoted"
    CANCELLED = "cancelled"


class ExtractionSource(enum.StrEnum):
    FORM = "form"
    LLM = "llm"
    IMPORT = "import"


class MovingRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "moving_requests"

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )

    # --- Origin ---
    origin_line1: Mapped[str] = mapped_column(String(300), nullable=False)
    origin_city: Mapped[str] = mapped_column(String(120), nullable=False)
    origin_state: Mapped[str] = mapped_column(String(2), nullable=False)
    origin_zip: Mapped[str] = mapped_column(String(10), nullable=False)
    origin_floor: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    origin_has_elevator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    origin_stairs_flights: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Destination ---
    destination_line1: Mapped[str] = mapped_column(String(300), nullable=False)
    destination_city: Mapped[str] = mapped_column(String(120), nullable=False)
    destination_state: Mapped[str] = mapped_column(String(2), nullable=False)
    destination_zip: Mapped[str] = mapped_column(String(10), nullable=False)
    destination_floor: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    destination_has_elevator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    destination_stairs_flights: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Move details ---
    move_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_date_flexible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    home_size: Mapped[HomeSize] = mapped_column(
        SAEnum(HomeSize, native_enum=False, length=20, validate_strings=True), nullable=False
    )
    packing_service: Mapped[PackingService] = mapped_column(
        SAEnum(PackingService, native_enum=False, length=20, validate_strings=True),
        default=PackingService.NONE,
        nullable=False,
    )
    special_items: Mapped[list[Any]] = mapped_column(JSONType, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # --- Derived / provenance ---
    distance_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[RequestStatus] = mapped_column(
        SAEnum(RequestStatus, native_enum=False, length=20, validate_strings=True),
        default=RequestStatus.PENDING,
        nullable=False,
        index=True,
    )
    extracted_by: Mapped[ExtractionSource] = mapped_column(
        SAEnum(ExtractionSource, native_enum=False, length=20, validate_strings=True),
        default=ExtractionSource.FORM,
        nullable=False,
    )
    #: Set when this request was produced by editing an earlier one. Together with
    #: ``Quote.superseded_by_quote_id`` this preserves what the customer originally
    #: asked for, alongside what they changed it to.
    supersedes_request_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("moving_requests.id", ondelete="SET NULL"), nullable=True
    )

    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<MovingRequest {self.id} {self.home_size.value} on {self.move_date}>"
