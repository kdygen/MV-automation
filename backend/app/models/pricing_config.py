"""Versioned per-company pricing configuration storage.

Rows are **append-only**: editing pricing creates a new version and flips ``is_active``;
nothing is updated in place. Quotes reference the exact row that priced them, so an
owner can always answer "why was this quote $1,970?" months later.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID, JSONType


class PricingConfigRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "pricing_configs"
    __table_args__ = (UniqueConstraint("company_id", "version"),)

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<PricingConfigRow v{self.version} active={self.is_active}>"
