"""calibration layer

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-12

Three tables:

* ``calibration_models`` — versioned, per-company fitted models. Never born active.
* ``quote_calibrations`` — one row per quote recording what calibration decided,
  including deciding nothing. Separate from ``quotes`` because shadow mode records
  calibrations that were deliberately **not** applied.
* ``zip_distances`` — a cache of ZIP-to-ZIP driving distances so historical moves
  exported without mileage can still be priced by the engine. Intentionally not
  tenant-scoped: it holds road geometry, not company data.

Purely additive; no existing table is touched. RLS deny-all on the two tenant tables,
consistent with 0006-0012. ``zip_distances`` holds no tenant data and is left readable.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID, JSONType

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calibration_models",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("algorithm", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("params", JSONType, nullable=False),
        sa.Column("metrics", JSONType, nullable=False),
        sa.Column("n_train", sa.Integer(), nullable=False),
        sa.Column("training_window_start", sa.Date(), nullable=True),
        sa.Column("training_window_end", sa.Date(), nullable=True),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by_user_id", GUID(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calibration_models")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_calibration_models_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id"],
            ["users.id"],
            name=op.f("fk_calibration_models_activated_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_calibration_models_company_id_status", "calibration_models", ["company_id", "status"]
    )
    op.create_index(
        "ix_calibration_models_company_id_created_at",
        "calibration_models",
        ["company_id", "created_at"],
    )

    op.create_table(
        "quote_calibrations",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("quote_id", GUID(), nullable=False),
        sa.Column("model_id", GUID(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("base_hours", sa.Float(), nullable=False),
        sa.Column("base_total_cents", sa.BigInteger(), nullable=False),
        sa.Column("calibrated_hours", sa.Float(), nullable=False),
        sa.Column("calibrated_total_cents", sa.BigInteger(), nullable=False),
        sa.Column("factor", sa.Float(), nullable=False),
        sa.Column("raw_factor", sa.Float(), nullable=False),
        sa.Column("clamped", sa.Boolean(), nullable=False),
        sa.Column("cap", sa.Float(), nullable=True),
        sa.Column("support", sa.Integer(), nullable=False),
        sa.Column("segment", sa.String(length=40), nullable=True),
        sa.Column("fallback_reason", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quote_calibrations")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_quote_calibrations_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            name=op.f("fk_quote_calibrations_quote_id_quotes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["calibration_models.id"],
            name=op.f("fk_quote_calibrations_model_id_calibration_models"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("quote_id", name="uq_quote_calibrations_quote_id"),
    )
    op.create_index(
        "ix_quote_calibrations_company_id_created_at",
        "quote_calibrations",
        ["company_id", "created_at"],
    )

    op.create_table(
        "zip_distances",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("origin_zip", sa.String(length=10), nullable=False),
        sa.Column("destination_zip", sa.String(length=10), nullable=False),
        sa.Column("miles", sa.Float(), nullable=True),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zip_distances")),
        sa.UniqueConstraint("origin_zip", "destination_zip", name="uq_zip_distances_pair"),
    )
    op.create_index("ix_zip_distances_pair", "zip_distances", ["origin_zip", "destination_zip"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE calibration_models ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE quote_calibrations ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_zip_distances_pair", table_name="zip_distances")
    op.drop_table("zip_distances")
    op.drop_index("ix_quote_calibrations_company_id_created_at", table_name="quote_calibrations")
    op.drop_table("quote_calibrations")
    op.drop_index(
        "ix_calibration_models_company_id_created_at", table_name="calibration_models"
    )
    op.drop_index("ix_calibration_models_company_id_status", table_name="calibration_models")
    op.drop_table("calibration_models")
