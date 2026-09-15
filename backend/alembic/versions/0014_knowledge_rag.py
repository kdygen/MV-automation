"""semantic company knowledge (RAG)

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-14

Adds document storage and the chunk index that both manual knowledge entries and
uploaded documents feed. ``company_knowledge`` is deliberately untouched: it stays the
authored source of truth for manual entries and simply gains chunks alongside it.

On PostgreSQL this also enables ``vector`` (pgvector), creates an HNSW index for cosine
search, and a functional GIN index for the lexical arm of hybrid retrieval. All of that
is dialect-guarded — SQLite stores embeddings as JSON and scores in Python, so the test
suite needs no Postgres.

The lexical arm needs **no extension**: ``to_tsvector``, ``websearch_to_tsquery`` and
``ts_rank_cd`` are built into PostgreSQL. ``pg_trgm`` was considered for acronym matching
and is deliberately not enabled — that problem is solved by requiring every query term to
match, so asking an operator to install an extension nothing reads would be needless
surface area.

**Extension privileges:** ``CREATE EXTENSION`` requires rights the application role may
not have on a managed instance. The statement is written ``IF NOT EXISTS`` so enabling
the extension beforehand through the Supabase dashboard makes this migration a no-op.

RLS deny-all on both new tables, consistent with 0006-0013.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.types import GUID, JSONType
from app.db.vector import EMBEDDING_DIMENSIONS

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"

    if postgres:
        # Supabase ships extensions in the `extensions` schema, which is already on the
        # role's search_path — so the unqualified ``vector(1536)`` column below resolves,
        # and the HNSW index can name ``extensions.vector_cosine_ops``. Installing into
        # `public` instead would break that index. IF NOT EXISTS makes this idempotent
        # whether the extension was enabled here or from the dashboard.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions")

    op.create_table(
        "knowledge_documents",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("failure_reason", sa.String(length=300), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding_model", sa.String(length=60), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_documents")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_knowledge_documents_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_knowledge_documents_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("company_id", "source_hash", name="uq_knowledge_documents_source"),
        sa.UniqueConstraint("company_id", "content_hash", name="uq_knowledge_documents_content"),
    )
    op.create_index(
        "ix_knowledge_documents_company_id_status",
        "knowledge_documents",
        ["company_id", "status"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("document_id", GUID(), nullable=True),
        sa.Column("knowledge_entry_id", GUID(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("heading", sa.String(length=300), nullable=True),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", _embedding_column(postgres), nullable=True),
        sa.Column("embedding_model", sa.String(length=60), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("chunk_metadata", JSONType, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        sa.CheckConstraint(
            "(document_id IS NULL) <> (knowledge_entry_id IS NULL)",
            name="ck_knowledge_chunks_single_parent",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_knowledge_chunks_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name=op.f("fk_knowledge_chunks_document_id_knowledge_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_entry_id"],
            ["company_knowledge.id"],
            name=op.f("fk_knowledge_chunks_knowledge_entry_id_company_knowledge"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_company_id_is_active", "knowledge_chunks", ["company_id", "is_active"]
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_index(
        "ix_knowledge_chunks_knowledge_entry_id", "knowledge_chunks", ["knowledge_entry_id"]
    )

    if postgres:
        # HNSW for cosine search. Built on an empty table, so it is instant here; the
        # alternative (ivfflat) needs representative data present to train its lists.
        op.execute(
            "CREATE INDEX ix_knowledge_chunks_embedding_hnsw ON knowledge_chunks "
            "USING hnsw (embedding extensions.vector_cosine_ops)"
        )
        # Functional index rather than a generated tsvector column: it keeps the ORM
        # model free of a Postgres-only column, so the same model is valid on SQLite.
        # The retrieval query must use this exact expression for the index to apply.
        op.execute(
            "CREATE INDEX ix_knowledge_chunks_fts ON knowledge_chunks "
            "USING gin (to_tsvector('english', content))"
        )
        op.execute("ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY")


def _embedding_column(postgres: bool) -> object:
    """``vector(1536)`` on PostgreSQL, JSON on SQLite — mirroring the ORM type."""
    if postgres:
        from pgvector.sqlalchemy import Vector

        return Vector(EMBEDDING_DIMENSIONS)
    return sa.JSON()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_fts")
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")

    op.drop_index("ix_knowledge_chunks_knowledge_entry_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_document_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_company_id_is_active", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_documents_company_id_status", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    # The extensions are intentionally left enabled: other objects may depend on them,
    # and dropping a shared extension on the way down is not this migration's business.
