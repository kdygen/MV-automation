"""Do SQLite and PostgreSQL retrieve the same things? (Step 7G)

Every other test in this suite runs on in-memory SQLite, which is what keeps the suite
fast and offline. That bargain is only honest if the two dialects actually agree, and
the places they could disagree are precisely the places the portable types exist:

* ``EmbeddingVector`` — a native ``vector(1536)`` on PostgreSQL, a JSON array on SQLite.
* Candidate generation — an HNSW cosine scan versus Python cosine over the tenant's rows.
* The lexical arm — ``websearch_to_tsquery`` versus the Step 3A token scorer.
* NULL semantics — the JSON-``null``-versus-SQL-``NULL`` trap that made every
  "is this chunk embedded?" count wrong on SQLite until it was fixed.

**What parity does and does not claim.** The vector arm is asserted to agree exactly:
same order, same scores to six decimals. The lexical arms are *different algorithms* and
are asserted to agree on semantics — conjunctive matching, tenant scoping, visibility —
not on ranking. Claiming identical lexical ranking would be false, and a test that
asserts something false is worse than no test.

Opt-in, because it needs a real PostgreSQL with pgvector::

    docker run -d --name mv-pgvector-test -e POSTGRES_PASSWORD=pw \\
        -e POSTGRES_DB=mvtest -p 55432:5432 pgvector/pgvector:pg16
    docker exec mv-pgvector-test psql -U postgres -d mvtest \\
        -c 'CREATE SCHEMA IF NOT EXISTS extensions;' \\
        -c 'ALTER DATABASE mvtest SET search_path TO "$user", public, extensions;'
    TEST_POSTGRES_URL=postgresql+psycopg://postgres:pw@localhost:55432/mvtest \\
        pytest tests/test_postgres_parity.py

The ``extensions`` schema and search_path mirror Supabase, so the migration under test is
the same one production will run rather than a simplified version of it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.vector import EMBEDDING_DIMENSIONS
from app.knowledge.comparison import distinct_titles
from app.models import Company, CompanyKnowledge, KnowledgeChunk, KnowledgeDocument
from app.providers.embeddings import FakeEmbeddingProvider
from app.services import indexing
from app.services.retrieval import (
    _lexical_candidates,
    _vector_candidates,
    hybrid_search,
)
from tests.knowledge_fixture import ACME_KNOWLEDGE, BRAVO_KNOWLEDGE, CASES

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set TEST_POSTGRES_URL to run PostgreSQL parity tests (see module docstring)",
)

PROVIDER = FakeEmbeddingProvider()
LOOSE = {"min_similarity": 0.05, "max_chunks": 5}

#: Fixed so the same row is the same row in both databases and ordering ties break
#: identically — retrieval sorts on ``KnowledgeChunk.id`` as its final tiebreaker, so
#: random ids would make "parity" depend on luck.
ACME_ID = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
BRAVO_ID = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")


def _entry_id(index: int, company: uuid.UUID) -> uuid.UUID:
    return uuid.UUID(f"{company.hex[:8]}-0000-4000-8000-{index:012d}")


# --------------------------------------------------------------------------- setup


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    """A PostgreSQL database migrated to head by Alembic itself.

    The migration runs in a subprocess so the cached ``Settings`` of this test process is
    never repointed at PostgreSQL — the rest of the suite must keep its SQLite database.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": POSTGRES_URL},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed on PostgreSQL:\n{result.stderr}")

    engine = create_engine(POSTGRES_URL, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def pg(pg_engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    _wipe(session)
    try:
        yield session
    finally:
        _wipe(session)
        session.close()


def _wipe(session: Session) -> None:
    """Remove only what these tests create. Never a schema drop."""
    session.execute(delete(KnowledgeChunk))
    session.execute(delete(KnowledgeDocument))
    session.execute(delete(CompanyKnowledge))
    session.execute(delete(Company).where(Company.id.in_([ACME_ID, BRAVO_ID])))
    session.commit()


def seed(session: Session) -> None:
    """Identical rows, identical ids, in whichever database is passed in."""
    for company_id, slug, name, rows in (
        (ACME_ID, "acme-parity", "Acme Movers", ACME_KNOWLEDGE),
        (BRAVO_ID, "bravo-parity", "Bravo Van Lines", BRAVO_KNOWLEDGE),
    ):
        session.add(
            Company(
                id=company_id, name=name, slug=slug, email=f"ops@{slug}.test", settings={}
            )
        )
        session.flush()
        for index, (category, title, content, keywords) in enumerate(rows):
            session.add(
                CompanyKnowledge(
                    id=_entry_id(index, company_id),
                    company_id=company_id,
                    category=category,
                    title=title,
                    content=content,
                    keywords=keywords,
                    is_active=True,
                )
            )
    session.commit()

    for entry in session.scalars(select(CompanyKnowledge).order_by(CompanyKnowledge.id)):
        indexing.index_knowledge_entry(session, entry, provider=PROVIDER)


@pytest.fixture()
def both(db: Session, pg: Session) -> tuple[Session, Session]:
    """The same knowledge base, indexed in SQLite and in PostgreSQL."""
    seed(db)
    seed(pg)
    return db, pg


def titles(session: Session, query: str, company: uuid.UUID = ACME_ID) -> tuple[str, ...]:
    return distinct_titles(
        [item.title for item in hybrid_search(session, company, query, provider=PROVIDER, **LOOSE)]
    )


# ------------------------------------------------------------------ the schema


class TestSchema:
    def test_the_migration_built_what_retrieval_needs(self, pg: Session) -> None:
        indexes = set(
            pg.scalars(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'knowledge_chunks'"
                )
            )
        )
        assert "ix_knowledge_chunks_embedding_hnsw" in indexes
        assert "ix_knowledge_chunks_fts" in indexes

    def test_the_vector_extension_lives_where_the_index_expects_it(
        self, pg: Session
    ) -> None:
        """0014 names ``extensions.vector_cosine_ops``; installing elsewhere breaks it."""
        schema = pg.scalar(
            text(
                "SELECT n.nspname FROM pg_extension e "
                "JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector'"
            )
        )
        assert schema == "extensions"

    def test_row_level_security_is_on_for_both_new_tables(self, pg: Session) -> None:
        enabled = dict(
            pg.execute(
                text(
                    "SELECT relname, relrowsecurity FROM pg_class WHERE relname IN "
                    "('knowledge_documents', 'knowledge_chunks')"
                )
            ).all()
        )
        assert enabled == {"knowledge_documents": True, "knowledge_chunks": True}

    def test_the_embedding_column_is_a_native_vector(self, pg: Session) -> None:
        dimensions = pg.scalar(
            text(
                "SELECT a.atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = 'knowledge_chunks' AND a.attname = 'embedding'"
            )
        )
        assert dimensions == EMBEDDING_DIMENSIONS


# ------------------------------------------------------------------ null parity


class TestNullParity:
    def test_a_missing_vector_is_null_on_both(self, both: tuple[Session, Session]) -> None:
        """The JSON-``null`` trap: it must be SQL NULL in both dialects, or every count lies."""
        for session in both:
            chunk = session.scalars(select(KnowledgeChunk)).first()
            assert chunk is not None
            chunk.embedding = None
            session.commit()
            assert (
                session.scalar(
                    select(KnowledgeChunk.id).where(
                        KnowledgeChunk.id == chunk.id, KnowledgeChunk.embedding.is_(None)
                    )
                )
                == chunk.id
            )

    def test_index_stats_agree(self, both: tuple[Session, Session]) -> None:
        sqlite, postgres = both
        assert indexing.index_stats(sqlite, ACME_ID) == indexing.index_stats(postgres, ACME_ID)


# ----------------------------------------------------------------- vector arm


class TestVectorArmIsIdentical:
    """The arm that must agree exactly: same maths, two implementations."""

    @pytest.mark.parametrize(
        "query",
        [
            "what happens if I back out three days before",
            "do you provide a certificate of insurance",
            "is there a fee for a third floor walk up",
            "how do I pay the deposit",
        ],
    )
    def test_same_order_and_same_scores(
        self, both: tuple[Session, Session], query: str
    ) -> None:
        sqlite, postgres = both
        vector = PROVIDER.embed([query]).vectors[0]

        left = _vector_candidates(sqlite, ACME_ID, vector, PROVIDER.model)
        right = _vector_candidates(postgres, ACME_ID, vector, PROVIDER.model)

        # Compared by content hash, not chunk id: ids are generated per insert, so the
        # same passage has different ids in the two databases. The hash is derived from
        # the text, which is what ordering ties break on for exactly this reason.
        assert [c.content_hash for c in left] == [c.content_hash for c in right]
        for a, b in zip(left, right, strict=True):
            assert a.similarity == pytest.approx(b.similarity, abs=1e-6)

    def test_a_model_mismatch_excludes_a_chunk_on_both(
        self, both: tuple[Session, Session]
    ) -> None:
        """Vectors from different models are not comparable and must never be compared."""
        vector = PROVIDER.embed(["deposit"]).vectors[0]
        for session in both:
            assert _vector_candidates(session, ACME_ID, vector, "some-other-model") == []


# ---------------------------------------------------------------- lexical arm


class TestLexicalArmAgreesOnSemantics:
    """Different algorithms; the contract they share is what is asserted."""

    def test_ties_break_identically(self, both: tuple[Session, Session]) -> None:
        """Ordering must not depend on a per-insert primary key."""
        sqlite, postgres = both
        vector = PROVIDER.embed(["deposit payment"]).vectors[0]

        def order(session: Session) -> list[str]:
            return [
                c.content_hash
                for c in _vector_candidates(session, ACME_ID, vector, PROVIDER.model)
            ]

        assert order(sqlite) == order(postgres)

    def test_an_exact_term_is_found_by_both(self, both: tuple[Session, Session]) -> None:
        for session in both:
            found = _lexical_candidates(session, ACME_ID, "certificate")
            assert any("Certificate" in c.title for c in found)

    def test_matching_is_conjunctive_on_both(
        self, both: tuple[Session, Session]
    ) -> None:
        """A query with one unmatched term must not match on the rest.

        This is the divergence that made false positives plateau at 0.41 during 7D:
        SQLite was ORing terms while PostgreSQL ANDed them.
        """
        for session in both:
            assert _lexical_candidates(session, ACME_ID, "certificate insurance") != []
            assert (
                _lexical_candidates(
                    session, ACME_ID, "certificate insurance zzzzunmatchedzzzz"
                )
                == []
            )

    def test_a_nonsense_query_matches_nothing_on_both(
        self, both: tuple[Session, Session]
    ) -> None:
        for session in both:
            assert _lexical_candidates(session, ACME_ID, "zzzzunmatchedzzzz") == []


# ------------------------------------------------------- isolation & visibility


class TestIsolationAndVisibility:
    def test_no_query_crosses_a_tenant_boundary_on_either(
        self, both: tuple[Session, Session]
    ) -> None:
        foreign = {title for _, title, _, _ in BRAVO_KNOWLEDGE}
        for session in both:
            for case in CASES:
                for evidence in hybrid_search(
                    session, ACME_ID, case.query, provider=PROVIDER, **LOOSE
                ):
                    assert evidence.title not in foreign

    def test_deactivating_hides_an_entry_on_both(
        self, both: tuple[Session, Session]
    ) -> None:
        query = "what happens if I back out three days before"
        for session in both:
            assert titles(session, query)
            entry = session.scalar(
                select(CompanyKnowledge).where(CompanyKnowledge.id == _entry_id(0, ACME_ID))
            )
            assert entry is not None
            entry.is_active = False
            session.commit()
            indexing.set_entry_chunks_active(session, entry)
            assert all("Cancellation" not in title for title in titles(session, query))

    def test_deleting_an_entry_removes_its_chunks_on_both(
        self, both: tuple[Session, Session]
    ) -> None:
        for session in both:
            removed = indexing.remove_entry_chunks(session, _entry_id(0, ACME_ID))
            assert removed > 0
            assert (
                session.scalars(
                    select(KnowledgeChunk.id).where(
                        KnowledgeChunk.knowledge_entry_id == _entry_id(0, ACME_ID)
                    )
                ).all()
                == []
            )


# ------------------------------------------------------------ end-to-end parity


class TestEndToEndRetrieval:
    def test_the_gate_admits_the_same_passages_on_both(
        self, both: tuple[Session, Session]
    ) -> None:
        """Fusion, dedup and gating are shared pure code; this proves the inputs match."""
        sqlite, postgres = both
        strict = {"min_similarity": 0.35, "max_chunks": 5}
        disagreements = []
        for case in CASES:
            left = {
                item.title
                for item in hybrid_search(
                    sqlite, ACME_ID, case.query, provider=PROVIDER, **strict
                )
            }
            right = {
                item.title
                for item in hybrid_search(
                    postgres, ACME_ID, case.query, provider=PROVIDER, **strict
                )
            }
            if left != right:
                disagreements.append((case.query, sorted(left), sorted(right)))
        assert not disagreements, f"{len(disagreements)}/{len(CASES)} queries differed"

    def test_declining_to_answer_is_the_same_decision_on_both(
        self, both: tuple[Session, Session]
    ) -> None:
        """An irrelevant question must return nothing in both dialects."""
        sqlite, postgres = both
        strict = {"min_similarity": 0.35, "max_chunks": 5}
        for case in CASES:
            if case.expected_titles:
                continue
            left = hybrid_search(sqlite, ACME_ID, case.query, provider=PROVIDER, **strict)
            right = hybrid_search(postgres, ACME_ID, case.query, provider=PROVIDER, **strict)
            assert bool(left) == bool(right), case.query

    def test_the_backfill_produces_identical_indexes(
        self, both: tuple[Session, Session]
    ) -> None:
        sqlite, postgres = both
        reports = [
            indexing.backfill_company(session, ACME_ID, provider=PROVIDER, force=True)
            for session in (sqlite, postgres)
        ]
        assert reports[0] == reports[1]

        def fingerprints(session: Session) -> list[tuple[int, str]]:
            return list(
                session.execute(
                    select(KnowledgeChunk.chunk_index, KnowledgeChunk.content_hash)
                    .where(KnowledgeChunk.company_id == ACME_ID)
                    .order_by(KnowledgeChunk.content_hash)
                ).all()
            )

        assert fingerprints(sqlite) == fingerprints(postgres)

    def test_a_second_backfill_is_a_no_op_on_both(
        self, both: tuple[Session, Session]
    ) -> None:
        for session in both:
            indexing.backfill_company(session, ACME_ID, provider=PROVIDER)
            again = indexing.backfill_company(session, ACME_ID, provider=PROVIDER)
            assert again.indexed == 0
            assert again.skipped == again.entries
