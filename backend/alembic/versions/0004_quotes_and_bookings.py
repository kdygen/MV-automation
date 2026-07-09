"""quotes and bookings

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-09

Quote lifecycle: the frozen priced offer (money in integer cents, full audit snapshot,
unguessable public token) and the booking created on acceptance (one per quote).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID, JSONType

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quotes",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("moving_request_id", GUID(), nullable=False),
        sa.Column("pricing_config_id", GUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount_min_cents", sa.BigInteger(), nullable=False),
        sa.Column("amount_max_cents", sa.BigInteger(), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("estimated_hours", sa.Float(), nullable=False),
        sa.Column("crew_size", sa.Integer(), nullable=False),
        sa.Column("engine_version", sa.String(length=40), nullable=False),
        sa.Column("line_items", JSONType, nullable=False),
        sa.Column("inputs_snapshot", JSONType, nullable=False),
        sa.Column("is_adjusted", sa.Boolean(), nullable=False),
        sa.Column("public_token", sa.String(length=64), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotes")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_quotes_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["moving_request_id"],
            ["moving_requests.id"],
            name=op.f("fk_quotes_moving_request_id_moving_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pricing_config_id"],
            ["pricing_configs.id"],
            name=op.f("fk_quotes_pricing_config_id_pricing_configs"),
            ondelete="RESTRICT",
        ),
    )
    op.create_index(op.f("ix_quotes_company_id"), "quotes", ["company_id"], unique=False)
    op.create_index(
        op.f("ix_quotes_moving_request_id"), "quotes", ["moving_request_id"], unique=False
    )
    op.create_index(op.f("ix_quotes_status"), "quotes", ["status"], unique=False)
    op.create_index(op.f("ix_quotes_public_token"), "quotes", ["public_token"], unique=True)

    op.create_table(
        "bookings",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("quote_id", GUID(), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("time_window", sa.String(length=50), nullable=True),
        sa.Column("crew_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_bookings_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            name=op.f("fk_bookings_quote_id_quotes"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("quote_id", name=op.f("uq_bookings_quote_id")),
    )
    op.create_index(op.f("ix_bookings_company_id"), "bookings", ["company_id"], unique=False)
    op.create_index(
        op.f("ix_bookings_scheduled_date"), "bookings", ["scheduled_date"], unique=False
    )
    op.create_index(op.f("ix_bookings_status"), "bookings", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_bookings_status"), table_name="bookings")
    op.drop_index(op.f("ix_bookings_scheduled_date"), table_name="bookings")
    op.drop_index(op.f("ix_bookings_company_id"), table_name="bookings")
    op.drop_table("bookings")
    op.drop_index(op.f("ix_quotes_public_token"), table_name="quotes")
    op.drop_index(op.f("ix_quotes_status"), table_name="quotes")
    op.drop_index(op.f("ix_quotes_moving_request_id"), table_name="quotes")
    op.drop_index(op.f("ix_quotes_company_id"), table_name="quotes")
    op.drop_table("quotes")
