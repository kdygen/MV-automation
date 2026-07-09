"""Platform user (staff) model.

This table stores the *authorization profile* for a member of a moving company —
their company, role, and display info. Credentials live in Supabase Auth, not here; the
``id`` mirrors the Supabase ``auth.users.id`` so a verified JWT's ``sub`` maps directly
to a row. Customers are **not** users; they are represented by ``leads``.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.types import GUID


class UserRole(enum.StrEnum):
    """Role of a staff member within their company."""

    OWNER = "owner"
    ADMIN = "admin"
    STAFF = "staff"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    # id is supplied from Supabase auth.users.id, not generated here.
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, native_enum=False, length=20, validate_strings=True),
        default=UserRole.STAFF,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<User {self.email!r} role={self.role.value}>"
