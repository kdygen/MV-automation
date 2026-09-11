"""historical move intelligence

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-10

Turns ``jobs`` into the canonical completed-move table for historical intelligence:
geography, access, services, outcome notes, and import provenance. One table rather
than a parallel ``historical_moves``, so platform completions and imported legacy jobs
are queried through the same shape.

Also relaxes the three actuals to nullable — real exports are incomplete, and the
importer enforces the "at least one outcome" rule instead. Safe to relax: this is a
widening change, and it is applied before any historical data exists.

RLS deny-all on PostgreSQL for the new table, consistent with 0006-0011.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.base import NAMING_CONVENTION
from app.db.types import GUID, JSONType

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels = None
depends_on = None

#: (name, type, nullable) for every column added to ``jobs``.
NEW_JOB_COLUMNS: list[tuple[str, object]] = [
    # geography
    ("origin_city", sa.String(length=120)),
    ("origin_state", sa.String(length=2)),
    ("origin_zip", sa.String(length=10)),
    ("destination_city", sa.String(length=120)),
    ("destination_state", sa.String(length=2)),
    ("destination_zip", sa.String(length=10)),
    # opt-in street addresses plus their derived grouping key
    ("origin_line1", sa.String(length=300)),
    ("destination_line1", sa.String(length=300)),
    ("origin_building_key", sa.String(length=320)),
    ("destination_building_key", sa.String(length=320)),
    # access
    ("origin_floor", sa.Integer()),
    ("origin_has_elevator", sa.Boolean()),
    ("origin_stairs_flights", sa.Integer()),
    ("destination_floor", sa.Integer()),
    ("destination_has_elevator", sa.Boolean()),
    ("destination_stairs_flights", sa.Integer()),
    ("long_carry", sa.Boolean()),
    ("parking_difficulty", sa.String(length=20)),
    # services / items
    ("move_type", sa.String(length=20)),
    ("special_items", JSONType),
    ("has_storage", sa.Boolean()),
    # operations / financial
    ("quoted_crew_size", sa.Integer()),
    ("additional_charges_cents", sa.BigInteger()),
    # outcome
    ("completed_at", sa.DateTime(timezone=True)),
    ("delay_minutes", sa.Integer()),
    ("issue_tags", JSONType),
    ("problem_notes", sa.Text()),
    ("building_notes", sa.Text()),
    ("change_notes", sa.Text()),
    ("variance_reason", sa.String(length=300)),
    # provenance
    ("import_batch_id", GUID()),
    ("external_ref", sa.String(length=120)),
    ("row_hash", sa.String(length=64)),
]


def upgrade() -> None:
    # Created first: ``jobs.import_batch_id`` references it.
    op.create_table(
        "job_import_batches",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=10), nullable=False),
        sa.Column("column_mapping", JSONType, nullable=False),
        sa.Column("parse_options", JSONType, nullable=False),
        sa.Column("row_count_total", sa.Integer(), nullable=False),
        sa.Column("row_count_imported", sa.Integer(), nullable=False),
        sa.Column("row_count_skipped", sa.Integer(), nullable=False),
        sa.Column("row_count_rejected", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", GUID(), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_import_batches")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_job_import_batches_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_job_import_batches_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_job_import_batches_company_id_created_at",
        "job_import_batches",
        ["company_id", "created_at"],
    )

    # Batch mode: SQLite cannot ALTER a column's nullability or add a constraint, so it
    # rebuilds the table; PostgreSQL gets plain ALTERs.
    with op.batch_alter_table("jobs", naming_convention=NAMING_CONVENTION) as batch:
        for name, column_type in NEW_JOB_COLUMNS:
            batch.add_column(sa.Column(name, column_type, nullable=True))

        batch.alter_column("actual_hours", existing_type=sa.Float(), nullable=True)
        batch.alter_column("actual_crew_size", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("actual_total_cents", existing_type=sa.BigInteger(), nullable=True)

        batch.create_foreign_key(
            op.f("fk_jobs_import_batch_id_job_import_batches"),
            "job_import_batches",
            ["import_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_jobs_company_id_external_ref", ["company_id", "external_ref"]
        )
        batch.create_unique_constraint("uq_jobs_company_id_row_hash", ["company_id", "row_hash"])

    op.create_index("ix_jobs_company_id_move_date", "jobs", ["company_id", "move_date"])
    op.create_index(
        "ix_jobs_company_id_origin_building_key", "jobs", ["company_id", "origin_building_key"]
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE job_import_batches ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_jobs_company_id_origin_building_key", table_name="jobs")
    op.drop_index("ix_jobs_company_id_move_date", table_name="jobs")

    with op.batch_alter_table("jobs", naming_convention=NAMING_CONVENTION) as batch:
        batch.drop_constraint("uq_jobs_company_id_row_hash", type_="unique")
        batch.drop_constraint("uq_jobs_company_id_external_ref", type_="unique")
        batch.drop_constraint(
            op.f("fk_jobs_import_batch_id_job_import_batches"), type_="foreignkey"
        )
        for name, _ in reversed(NEW_JOB_COLUMNS):
            batch.drop_column(name)
        # Narrowing back would fail on any row that used the new optionality, so the
        # downgrade restores NOT NULL only after those columns are gone.
        batch.alter_column("actual_total_cents", existing_type=sa.BigInteger(), nullable=False)
        batch.alter_column("actual_crew_size", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("actual_hours", existing_type=sa.Float(), nullable=False)

    op.drop_index("ix_job_import_batches_company_id_created_at", table_name="job_import_batches")
    op.drop_table("job_import_batches")
