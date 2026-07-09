"""Lead model.

A lead is a potential customer who contacted a company (via the public form, the AI
chat, or a CSV import). Leads are tenant-scoped and move through a simple status
funnel as their request is quoted, accepted, booked, and completed.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID


class LeadSource(enum.StrEnum):
    FORM = "form"
    CHAT = "chat"
    IMPORT = "import"


class LeadStatus(enum.StrEnum):
    NEW = "new"
    QUOTED = "quoted"
    ACCEPTED = "accepted"
    BOOKED = "booked"
    COMPLETED = "completed"
    LOST = "lost"


class Lead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "leads"

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source: Mapped[LeadSource] = mapped_column(
        SAEnum(LeadSource, native_enum=False, length=20, validate_strings=True),
        default=LeadSource.FORM,
        nullable=False,
    )
    status: Mapped[LeadStatus] = mapped_column(
        SAEnum(LeadStatus, native_enum=False, length=20, validate_strings=True),
        default=LeadStatus.NEW,
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Lead {self.email!r} status={self.status.value}>"
