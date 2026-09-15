"""Alembic migration environment.

The database URL comes from application settings (env vars / .env) rather than
alembic.ini, so migrations always target the same database the app is configured for.
Autogenerate compares against ``Base.metadata``; importing ``app.models`` populates it.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  (populates Base.metadata)
from alembic import context
from app.core.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = get_settings().database_url.replace("%", "%%")
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata

#: Indexes that exist in PostgreSQL but cannot exist in the ORM metadata.
#:
#: Both are created by raw SQL in migration 0014 — an HNSW index over a pgvector column
#: and a functional GIN index over ``to_tsvector('english', content)``. Neither has a
#: SQLAlchemy ``Index`` to compare against, and keeping the model free of them is what
#: lets the same models run on SQLite in the test suite.
#:
#: Without this filter, autogenerate sees two indexes it does not recognise and proposes
#: ``DROP INDEX`` for both — so the next person to run ``alembic revision --autogenerate``
#: against PostgreSQL would generate a migration that quietly deletes the two indexes
#: semantic search depends on. Excluding them by name makes autogenerate leave them alone.
UNMANAGED_INDEXES = frozenset(
    {"ix_knowledge_chunks_embedding_hnsw", "ix_knowledge_chunks_fts"}
)


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """Hide raw-SQL indexes from autogenerate's comparison."""
    return not (type_ == "index" and name in UNMANAGED_INDEXES)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
