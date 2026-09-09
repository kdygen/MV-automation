"""conversations and messages

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09

Persistence for the post-quote sales agent: ``conversations`` (one chat thread per
quote) and ``messages`` (the append-only transcript, including tool calls).

Only non-derivable state is stored. ``conversations.company_id`` is the tenant
boundary; the lead is reached via quote → moving_request → lead, and a message's
tenant via its conversation — so no copy of that state can drift.

On PostgreSQL both tables get RLS enabled with no policies, consistent with migration
0006: the anon key reaches nothing and all access flows through the application.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID, JSONType

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None

NEW_TABLES = ["conversations", "messages"]


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("quote_id", GUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_conversations_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            name=op.f("fk_conversations_quote_id_quotes"),
            ondelete="CASCADE",
        ),
        # One conversation per quote: identity + race-safe get-or-create.
        sa.UniqueConstraint("quote_id", name=op.f("uq_conversations_quote_id")),
    )
    op.create_index(
        op.f("ix_conversations_company_id"), "conversations", ["company_id"], unique=False
    )

    op.create_table(
        "messages",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("conversation_id", GUID(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("tool_args", JSONType, nullable=True),
        sa.Column("tool_result", JSONType, nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
    )
    # Composite: filter by thread + order by time in a single index scan.
    op.create_index(
        "ix_messages_conversation_id_created_at",
        "messages",
        ["conversation_id", "created_at"],
        unique=False,
    )

    # Defense-in-depth, same as 0006. PostgreSQL-only; SQLite skips harmlessly.
    if op.get_bind().dialect.name == "postgresql":
        for table in NEW_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_id_created_at", table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_conversations_company_id"), table_name="conversations")
    op.drop_table("conversations")
