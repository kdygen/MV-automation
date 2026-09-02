"""enable row level security (deny-all)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-02

Defense-in-depth for Supabase: enable RLS on every business table with **no policies**,
which denies all access to PostgREST's ``anon``/``authenticated`` roles (i.e. anyone
holding the public anon key). Our FastAPI backend is unaffected — it connects as the
table owner, which bypasses RLS — so all data access continues to flow through the
one place that enforces tenancy: the application.

PostgreSQL-only; a SQLite target (local tinkering) skips harmlessly.
"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None

TABLES = [
    "companies",
    "users",
    "leads",
    "moving_requests",
    "pricing_configs",
    "quotes",
    "bookings",
    "jobs",
]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
