"""Customer change-request audit trail.

One row per *confirmed* customer edit, recording what was asked for and which revision
resulted. Together with the quote chain this answers the auditability question in full:
the previous quote holds the original inputs and price, ``requested_changes`` holds
what the customer asked to change, and the resulting quote holds the recalculated
result and the config version that produced it.

Written at confirmation, not at preview, on purpose. The preview is a pure function of
(request + changes, pricing config version), all three of which are recorded here or on
the quotes themselves — so what the customer saw before confirming can be recomputed
exactly rather than duplicated into storage.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID, JSONType


class ChangeKind(enum.StrEnum):
    DATE = "date"  # move date only
    DETAILS = "details"  # any other priced input


class QuoteChangeRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A confirmed customer edit and the revision it produced."""

    __tablename__ = "quote_change_requests"
    __table_args__ = (
        Index(
            "ix_quote_change_requests_company_id_created_at", "company_id", "created_at"
        ),
        Index("ix_quote_change_requests_previous_quote_id", "previous_quote_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    previous_quote_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    resulting_quote_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[ChangeKind] = mapped_column(
        SAEnum(ChangeKind, native_enum=False, length=20, validate_strings=True),
        nullable=False,
    )
    #: Exactly the fields the customer changed, as submitted (already validated).
    requested_changes: Mapped[dict[str, Any]] = mapped_column(
        JSONType, default=dict, nullable=False
    )
    #: When the customer clicked Confirm — the moment consent was given.
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Free-text label of the source, e.g. "quote_page". Never a customer identifier.
    source: Mapped[str] = mapped_column(String(40), default="quote_page", nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<QuoteChangeRequest {self.kind.value} -> {self.resulting_quote_id}>"
