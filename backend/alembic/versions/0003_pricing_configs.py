"""pricing_configs

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-09

Append-only, versioned per-company pricing configuration. Unique (company_id, version);
the single-active-version invariant is enforced in the service layer.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID, JSONType

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pricing_configs",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("config", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing_configs")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_pricing_configs_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("company_id", "version", name=op.f("uq_pricing_configs_company_id")),
    )
    op.create_index(
        op.f("ix_pricing_configs_company_id"), "pricing_configs", ["company_id"], unique=False
    )
    op.create_index(
        op.f("ix_pricing_configs_is_active"), "pricing_configs", ["is_active"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pricing_configs_is_active"), table_name="pricing_configs")
    op.drop_index(op.f("ix_pricing_configs_company_id"), table_name="pricing_configs")
    op.drop_table("pricing_configs")
