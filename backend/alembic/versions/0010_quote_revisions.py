"""quote revisions and change requests

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-10

Makes quotes append-only across customer edits. A confirmed edit creates the next
revision and links the old row forward, so every price a customer was shown keeps its
own inputs snapshot, engine version, and pricing-config pointer.

``quotes.status`` is a plain VARCHAR with no CHECK constraint (see 0004), so the new
``superseded`` value needs no DDL.

RLS deny-all on PostgreSQL for the new table, consistent with 0006-0009.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.base import NAMING_CONVENTION
from app.db.types import GUID, JSONType

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode throughout: SQLite (tests, local preview) cannot ALTER TABLE ADD
    # CONSTRAINT and has to rebuild the table, while PostgreSQL gets plain ALTERs.
    #
    # Existing quotes are all revision 1 heads; the server default backfills them and
    # is kept so raw inserts outside the ORM stay valid.
    with op.batch_alter_table("quotes", naming_convention=NAMING_CONVENTION) as batch:
        batch.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(sa.Column("superseded_by_quote_id", GUID(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_quotes_superseded_by_quote_id_quotes"),
            "quotes",
            ["superseded_by_quote_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("moving_requests", naming_convention=NAMING_CONVENTION) as batch:
        batch.add_column(sa.Column("supersedes_request_id", GUID(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_moving_requests_supersedes_request_id_moving_requests"),
            "moving_requests",
            ["supersedes_request_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "quote_change_requests",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("previous_quote_id", GUID(), nullable=False),
        sa.Column("resulting_quote_id", GUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("requested_changes", JSONType, nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quote_change_requests")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_quote_change_requests_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["previous_quote_id"],
            ["quotes.id"],
            name=op.f("fk_quote_change_requests_previous_quote_id_quotes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resulting_quote_id"],
            ["quotes.id"],
            name=op.f("fk_quote_change_requests_resulting_quote_id_quotes"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_quote_change_requests_company_id_created_at",
        "quote_change_requests",
        ["company_id", "created_at"],
    )
    op.create_index(
        "ix_quote_change_requests_previous_quote_id",
        "quote_change_requests",
        ["previous_quote_id"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE quote_change_requests ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index(
        "ix_quote_change_requests_previous_quote_id", table_name="quote_change_requests"
    )
    op.drop_index(
        "ix_quote_change_requests_company_id_created_at", table_name="quote_change_requests"
    )
    op.drop_table("quote_change_requests")

    with op.batch_alter_table("moving_requests", naming_convention=NAMING_CONVENTION) as batch:
        batch.drop_constraint(
            op.f("fk_moving_requests_supersedes_request_id_moving_requests"),
            type_="foreignkey",
        )
        batch.drop_column("supersedes_request_id")

    with op.batch_alter_table("quotes", naming_convention=NAMING_CONVENTION) as batch:
        batch.drop_constraint(op.f("fk_quotes_superseded_by_quote_id_quotes"), type_="foreignkey")
        batch.drop_column("superseded_by_quote_id")
        batch.drop_column("revision")
