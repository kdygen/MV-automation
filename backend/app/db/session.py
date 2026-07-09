"""Engine and session management.

Exposes a lazily-created engine + ``sessionmaker`` and a :func:`get_db` FastAPI
dependency that yields a session and guarantees cleanup. The engine is built from
``settings.database_url`` so tests can point it at an in-memory SQLite database.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """Create (once) and return the SQLAlchemy engine for the configured database."""
    settings = get_settings()
    url = settings.database_url
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # Allow the file/in-memory DB to be shared across FastAPI's threadpool.
        connect_args["check_same_thread"] = False
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a database session and always closes it."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
