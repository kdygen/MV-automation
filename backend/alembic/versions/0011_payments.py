"""payments and webhook idempotency

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-10

Records checkout attempts and the provider events already acted on. The unique
constraint on ``(provider, event_id)`` is what makes duplicate webhook delivery a
no-op instead of a second booking.

RLS deny-all on PostgreSQL for both tables, consistent with 0006-0010.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("quote_id", GUID(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_session_id", sa.String(length=255), nullable=False),
        sa.Column("provider_payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_payments_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            name=op.f("fk_payments_quote_id_quotes"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("provider_session_id", name="uq_payments_provider_session_id"),
    )
    op.create_index("ix_payments_quote_id", "payments", ["quote_id"])
    op.create_index("ix_payments_company_id", "payments", ["company_id"])
    op.create_index(op.f("ix_payments_status"), "payments", ["status"])

    op.create_table(
        "processed_webhook_events",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processed_webhook_events")),
        sa.UniqueConstraint(
            "provider", "event_id", name="uq_processed_webhook_events_provider_event_id"
        ),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE payments ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE processed_webhook_events ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("processed_webhook_events")
    op.drop_index(op.f("ix_payments_status"), table_name="payments")
    op.drop_index("ix_payments_company_id", table_name="payments")
    op.drop_index("ix_payments_quote_id", table_name="payments")
    op.drop_table("payments")
