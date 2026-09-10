"""company knowledge

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-10

Tenant-specific policy/FAQ answers backing the agent's knowledge search. Stored as
rows rather than in ``companies.settings`` so entries can be filtered server-side,
activated individually, and edited without read-modify-write collisions.

RLS deny-all on PostgreSQL, consistent with migrations 0006 and 0007.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_knowledge",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("keywords", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_knowledge")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_company_knowledge_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("company_id", "title", name="uq_company_knowledge_company_id_title"),
    )
    op.create_index(
        "ix_company_knowledge_company_id_is_active",
        "company_knowledge",
        ["company_id", "is_active"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE company_knowledge ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_company_knowledge_company_id_is_active", table_name="company_knowledge")
    op.drop_table("company_knowledge")
