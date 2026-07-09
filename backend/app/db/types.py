"""Portable SQLAlchemy column types that work on PostgreSQL and SQLite.

- :class:`GUID` stores UUIDs natively on PostgreSQL and as ``CHAR(36)`` on SQLite, always
  returning ``uuid.UUID`` objects to Python.
- :data:`JSONType` uses ``JSONB`` on PostgreSQL and the generic ``JSON`` on SQLite.

Using these keeps a single set of models valid across the production database and the
in-memory test database, avoiding schema drift between the two.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import CHAR, JSON, TypeDecorator

# JSONB on Postgres, JSON on SQLite.
JSONType = JSONB().with_variant(JSON(), "sqlite")


class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Values are always exposed to Python as :class:`uuid.UUID`.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value: Any, dialect: Any) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))
