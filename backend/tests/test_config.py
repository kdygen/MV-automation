"""Tests for settings validation — the production database guard."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

PG_URL = "postgresql+psycopg://user:pw@db.example.com:5432/postgres"


def test_production_with_postgres_boots() -> None:
    settings = Settings(environment="production", database_url=PG_URL, _env_file=None)
    assert settings.is_production


def test_production_with_sqlite_refuses_to_start() -> None:
    with pytest.raises(ValidationError, match="requires DATABASE_URL"):
        Settings(
            environment="production",
            database_url="sqlite+pysqlite:///./mv_local.db",
            _env_file=None,
        )


def test_production_with_default_database_refuses_to_start() -> None:
    """Forgetting DATABASE_URL entirely must not silently boot on the SQLite default."""
    with pytest.raises(ValidationError, match="requires DATABASE_URL"):
        Settings(environment="production", _env_file=None)


def test_development_with_sqlite_is_fine() -> None:
    settings = Settings(
        environment="development",
        database_url="sqlite+pysqlite:///./mv_local.db",
        _env_file=None,
    )
    assert not settings.is_production
