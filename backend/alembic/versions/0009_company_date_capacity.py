"""company date capacity

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-10

Sparse per-date availability overrides. A row exists only where a company departs from
its default daily capacity, so an empty table means "normal capacity every day".

RLS deny-all on PostgreSQL, consistent with migrations 0006-0008.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_date_capacity",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("max_moves", sa.Integer(), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_date_capacity")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_company_date_capacity_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("company_id", "date", name="uq_company_date_capacity_company_id_date"),
    )
    op.create_index(
        "ix_company_date_capacity_company_id_date",
        "company_date_capacity",
        ["company_id", "date"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE company_date_capacity ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_company_date_capacity_company_id_date", table_name="company_date_capacity")
    op.drop_table("company_date_capacity")
