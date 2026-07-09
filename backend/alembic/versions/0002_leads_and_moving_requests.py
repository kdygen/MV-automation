"""leads and moving_requests

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-09

Intake domain: ``leads`` (potential customers, tenant-scoped) and ``moving_requests``
(the structured MoveSpec that feeds the pricing engine). Enum columns are stored as
short strings (native_enum=False in the models) for portability.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID, JSONType

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_leads_company_id_companies"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(op.f("ix_leads_company_id"), "leads", ["company_id"], unique=False)
    op.create_index(op.f("ix_leads_status"), "leads", ["status"], unique=False)

    op.create_table(
        "moving_requests",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("lead_id", GUID(), nullable=False),
        sa.Column("origin_line1", sa.String(length=300), nullable=False),
        sa.Column("origin_city", sa.String(length=120), nullable=False),
        sa.Column("origin_state", sa.String(length=2), nullable=False),
        sa.Column("origin_zip", sa.String(length=10), nullable=False),
        sa.Column("origin_floor", sa.Integer(), nullable=False),
        sa.Column("origin_has_elevator", sa.Boolean(), nullable=False),
        sa.Column("origin_stairs_flights", sa.Integer(), nullable=False),
        sa.Column("destination_line1", sa.String(length=300), nullable=False),
        sa.Column("destination_city", sa.String(length=120), nullable=False),
        sa.Column("destination_state", sa.String(length=2), nullable=False),
        sa.Column("destination_zip", sa.String(length=10), nullable=False),
        sa.Column("destination_floor", sa.Integer(), nullable=False),
        sa.Column("destination_has_elevator", sa.Boolean(), nullable=False),
        sa.Column("destination_stairs_flights", sa.Integer(), nullable=False),
        sa.Column("move_date", sa.Date(), nullable=False),
        sa.Column("is_date_flexible", sa.Boolean(), nullable=False),
        sa.Column("home_size", sa.String(length=20), nullable=False),
        sa.Column("packing_service", sa.String(length=20), nullable=False),
        sa.Column("special_items", JSONType, nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("distance_miles", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("extracted_by", sa.String(length=20), nullable=False),
        sa.Column("raw_payload", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moving_requests")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_moving_requests_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            name=op.f("fk_moving_requests_lead_id_leads"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_moving_requests_company_id"), "moving_requests", ["company_id"], unique=False
    )
    op.create_index(
        op.f("ix_moving_requests_lead_id"), "moving_requests", ["lead_id"], unique=False
    )
    op.create_index(
        op.f("ix_moving_requests_status"), "moving_requests", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_moving_requests_status"), table_name="moving_requests")
    op.drop_index(op.f("ix_moving_requests_lead_id"), table_name="moving_requests")
    op.drop_index(op.f("ix_moving_requests_company_id"), table_name="moving_requests")
    op.drop_table("moving_requests")
    op.drop_index(op.f("ix_leads_status"), table_name="leads")
    op.drop_index(op.f("ix_leads_company_id"), table_name="leads")
    op.drop_table("leads")
