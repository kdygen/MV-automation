"""Company (tenant) model.

A company is the tenant boundary of the platform. Every other business row references a
company via ``company_id``. The ``slug`` is used in public funnel URLs
(``/{slug}/quote``); ``settings`` holds per-tenant configuration such as whether quotes
require owner review before being sent, branding, and quote validity windows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JSONType


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Company {self.slug!r}>"
