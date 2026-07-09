"""jobs

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-09

Completed moves with actuals — the ML training table. Denormalized move features so
platform-completed and CSV-imported jobs share one shape; quoted values kept alongside
actuals for accuracy tracking.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("booking_id", GUID(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("move_date", sa.Date(), nullable=False),
        sa.Column("home_size", sa.String(length=20), nullable=False),
        sa.Column("packing_service", sa.String(length=20), nullable=False),
        sa.Column("distance_miles", sa.Float(), nullable=True),
        sa.Column("quoted_hours", sa.Float(), nullable=True),
        sa.Column("quoted_total_cents", sa.BigInteger(), nullable=True),
        sa.Column("actual_hours", sa.Float(), nullable=False),
        sa.Column("actual_crew_size", sa.Integer(), nullable=False),
        sa.Column("actual_total_cents", sa.BigInteger(), nullable=False),
        sa.Column("actual_volume_cuft", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_jobs_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_jobs_booking_id_bookings"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("booking_id", name=op.f("uq_jobs_booking_id")),
    )
    op.create_index(op.f("ix_jobs_company_id"), "jobs", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_jobs_company_id"), table_name="jobs")
    op.drop_table("jobs")
