"""Shadow retrieval and the manual-entry index (Step 7G preparation).

Two things are being pinned down here, and they matter for different reasons.

The **shadow architecture** must be provably inert: with it on, the customer gets byte-
identical answers to the ones they got before, and with hybrid mode on, a failure in the
semantic path falls back rather than surfacing. Those are the properties that make it
safe to run against real traffic before any decision has been made.

The **manual-entry index** must stay in step with the entries the owner edits, and the
backfill must be cheap to re-run — an operator whose first run was interrupted will run
it again, and it must not re-embed a knowledge base that has not changed.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.knowledge.comparison import (
    Agreement,
    ShadowStats,
    compare,
    distinct_titles,
    fingerprint,
)
from app.models import Company, CompanyKnowledge, KnowledgeChunk
from app.providers.embeddings import EmbeddingError, EmbeddingResult, FakeEmbeddingProvider
from app.schemas.knowledge import KnowledgeEntryIn, KnowledgeEntryPatch
from app.services import indexing, knowledge_search
from app.services import knowledge as knowledge_service
from app.services.knowledge_search import search_for_agent

PROVIDER = FakeEmbeddingProvider()


class _CountingProvider:
    """Counts embedding calls so "did this cost an API request?" is testable."""

    model = "fake-embedding-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.texts = 0

    def embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls += 1
        self.texts += len(texts)
        return PROVIDER.embed(texts)


class _BrokenProvider:
    model = "fake-embedding-v1"

    def embed(self, texts: list[str]) -> EmbeddingResult:
        raise EmbeddingError("provider unavailable")


def settings_for(
    mode: str = "keyword", *, shadow: bool = False, log_queries: bool = False
) -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+pysqlite://",
        knowledge_retrieval_mode=mode,  # type: ignore[arg-type]
        knowledge_shadow_enabled=shadow,
        knowledge_shadow_log_queries=log_queries,
        _env_file=None,
    )


@pytest.fixture()
def entries(db: Session, company: Company) -> list[CompanyKnowledge]:
    """A small answer book, indexed — the state a backfilled tenant is in."""
    rows = [
        CompanyKnowledge(
            company_id=company.id,
            category="policy",
            title="Cancellation policy",
            content=(
                "You may cancel at no charge up to 72 hours before your move. Inside "
                "that window we retain the deposit."
            ),
            keywords="cancel, cancellation, refund, call off",
            is_active=True,
        ),
        CompanyKnowledge(
            company_id=company.id,
            category="insurance",
            title="Certificate of Insurance",
            content=(
                "We provide Certificates of Insurance for buildings that require one, "
                "at no charge, with five business days notice."
            ),
            keywords="COI, certificate of insurance, building management",
            is_active=True,
        ),
    ]
    db.add_all(rows)
    db.commit()
    for row in rows:
        indexing.index_knowledge_entry(db, row, provider=PROVIDER)
    return rows


# ----------------------------------------------------------------- comparison


class TestComparison:
    def test_identical_answers_are_recognised(self) -> None:
        result = compare("q", ["A", "B"], ["A", "B"])
        assert result.agreement is Agreement.IDENTICAL
        assert result.top1_match is True
        assert result.jaccard == 1.0
        assert not result.disagrees

    def test_the_same_answers_in_a_different_order_is_its_own_verdict(self) -> None:
        result = compare("q", ["A", "B"], ["B", "A"])
        assert result.agreement is Agreement.SAME_SET
        assert result.top1_match is False
        assert not result.disagrees

    def test_partial_and_total_disagreement_are_distinguished(self) -> None:
        assert compare("q", ["A", "B"], ["B", "C"]).agreement is Agreement.OVERLAP
        assert compare("q", ["A"], ["Z"]).agreement is Agreement.DISJOINT

    def test_both_declining_counts_as_agreement(self) -> None:
        result = compare("q", [], [])
        assert result.agreement is Agreement.BOTH_EMPTY
        assert not result.disagrees

    def test_a_one_sided_answer_names_which_side(self) -> None:
        assert compare("q", ["A"], []).agreement is Agreement.KEYWORD_ONLY
        assert compare("q", [], ["A"]).agreement is Agreement.HYBRID_ONLY

    def test_an_outage_is_not_reported_as_a_miss(self) -> None:
        """Averaging an outage into "found nothing" would hide both findings."""
        result = compare("q", ["A"], None)
        assert result.agreement is Agreement.HYBRID_FAILED
        assert result.hybrid_count == 0

    def test_titles_are_deduped_and_capped_so_the_units_are_comparable(self) -> None:
        # Hybrid returns passages; one entry can produce several. Comparing raw lists
        # would report disagreement on every query.
        assert distinct_titles(["A", "a", "B", "C", "D"]) == ("A", "B", "C")

    def test_the_fingerprint_is_stable_and_carries_no_text(self) -> None:
        assert fingerprint("  How do I  CANCEL? ") == fingerprint("how do i cancel?")
        assert fingerprint("cancel") != fingerprint("insurance")
        assert len(fingerprint("cancel")) == 12

    def test_a_comparison_never_carries_the_customers_question(self) -> None:
        fields = compare("my address is 12 Elm St and my phone is 555-1234", ["A"], ["A"])
        assert "12 Elm St" not in str(fields.as_log_fields())
        assert "query" not in fields.as_log_fields()

    def test_stats_summarise_a_run(self) -> None:
        stats = ShadowStats.empty()
        for left, right in ((["A"], ["A"]), (["A"], ["B"]), (["A"], [])):
            stats.record(compare("q", left, right))
        assert stats.total == 3
        assert stats.top1_agreement == pytest.approx(1 / 3)
        assert stats.disagreement_rate == pytest.approx(2 / 3)
        assert "queries=3" in stats.summary()


# ------------------------------------------------------------ shadow serving


class TestShadowServing:
    def test_keyword_mode_serves_keyword_and_runs_nothing_else(
        self, db: Session, company: Company, entries: list[CompanyKnowledge]
    ) -> None:
        provider = _CountingProvider()
        outcome = search_for_agent(
            db, company.id, "what is your cancellation policy?",
            settings=settings_for(), provider=provider,
        )
        assert outcome.source == "keyword"
        assert outcome.comparison is None
        assert provider.calls == 0, "no embedding may be paid for when nothing shadows"
        assert outcome.answers[0].title == "Cancellation policy"

    def test_shadow_mode_changes_nothing_the_customer_sees(
        self, db: Session, company: Company, entries: list[CompanyKnowledge]
    ) -> None:
        """The property that makes it safe to switch on against real traffic."""
        question = "what is your cancellation policy?"
        before = search_for_agent(db, company.id, question, settings=settings_for())
        after = search_for_agent(
            db, company.id, question,
            settings=settings_for(shadow=True), provider=PROVIDER,
        )
        assert after.source == "keyword"
        assert after.answers == before.answers
        assert after.comparison is not None

    def test_shadow_mode_actually_runs_the_other_retriever(
        self, db: Session, company: Company, entries: list[CompanyKnowledge]
    ) -> None:
        provider = _CountingProvider()
        outcome = search_for_agent(
            db, company.id, "can I get a COI?",
            settings=settings_for(shadow=True), provider=provider,
        )
        assert provider.calls == 1
        assert outcome.comparison is not None
        assert outcome.comparison.hybrid_count > 0

    def test_a_shadow_failure_never_reaches_the_customer(
        self, db: Session, company: Company, entries: list[CompanyKnowledge], monkeypatch
    ) -> None:
        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("vector index is on fire")

        monkeypatch.setattr(knowledge_search, "hybrid_search", explode)
        outcome = search_for_agent(
            db, company.id, "cancellation", settings=settings_for(shadow=True),
            provider=PROVIDER,
        )
        assert outcome.source == "keyword"
        assert outcome.answers, "the served answer must be unaffected"
        assert outcome.comparison is not None
        assert outcome.comparison.agreement is Agreement.HYBRID_FAILED

    def test_a_tenant_with_no_index_is_never_charged_for_an_embedding(
        self, db: Session, company: Company
    ) -> None:
        """Nothing to retrieve, so embedding the question would buy exactly nothing."""
        db.add(
            CompanyKnowledge(
                company_id=company.id, category="policy", title="Cancellation policy",
                content="Cancel free up to 72 hours before your move.", is_active=True,
            )
        )
        db.commit()
        provider = _CountingProvider()
        outcome = search_for_agent(
            db, company.id, "cancel", settings=settings_for(shadow=True), provider=provider
        )
        assert provider.calls == 0
        assert outcome.comparison is not None
        assert outcome.comparison.agreement is Agreement.KEYWORD_ONLY


class TestHybridModeAndFallback:
    def test_hybrid_mode_serves_hybrid(
        self, db: Session, company: Company, entries: list[CompanyKnowledge]
    ) -> None:
        outcome = search_for_agent(
            db, company.id, "can I get a COI?",
            settings=settings_for("hybrid"), provider=PROVIDER,
        )
        assert outcome.source == "hybrid"
        assert any("Certificate" in answer.title for answer in outcome.answers)

    def test_hybrid_falls_back_to_keyword_when_retrieval_fails(
        self, db: Session, company: Company, entries: list[CompanyKnowledge], monkeypatch
    ) -> None:
        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("pgvector unavailable")

        monkeypatch.setattr(knowledge_search, "hybrid_search", explode)
        outcome = search_for_agent(
            db, company.id, "cancellation policy",
            settings=settings_for("hybrid"), provider=PROVIDER,
        )
        assert outcome.source == "keyword_fallback"
        assert outcome.answers[0].title == "Cancellation policy"

    def test_a_broken_embedding_provider_still_answers(
        self, db: Session, company: Company, entries: list[CompanyKnowledge]
    ) -> None:
        """hybrid_search degrades to its lexical arm; the customer is never told."""
        outcome = search_for_agent(
            db, company.id, "cancellation policy",
            settings=settings_for("hybrid"), provider=_BrokenProvider(),
        )
        assert outcome.source == "hybrid"
        assert outcome.answers

    def test_hybrid_finding_nothing_is_an_answer_not_a_failure(
        self, db: Session, company: Company, entries: list[CompanyKnowledge]
    ) -> None:
        """The gate declining to answer is the point of having a gate."""
        outcome = search_for_agent(
            db, company.id, "do you sell car insurance for boats?",
            settings=settings_for("hybrid"), provider=PROVIDER,
        )
        assert outcome.source == "hybrid", "an empty result must not trigger the fallback"
        assert outcome.answers == ()


class TestShadowLogging:
    def test_the_customers_question_is_not_logged_by_default(
        self, db: Session, company: Company, entries: list[CompanyKnowledge], caplog
    ) -> None:
        question = "my name is Dana Smith, can I cancel?"
        with caplog.at_level(logging.INFO):
            search_for_agent(
                db, company.id, question,
                settings=settings_for(shadow=True), provider=PROVIDER,
            )
        shadow_lines = [
            record.getMessage()
            for record in caplog.records
            if "knowledge_shadow" in record.getMessage()
        ]
        assert shadow_lines, "a comparison must be logged"
        assert all("Dana Smith" not in line for line in shadow_lines)
        assert all("query_fingerprint=" in line for line in shadow_lines)

    def test_question_text_is_logged_only_when_explicitly_enabled(
        self, db: Session, company: Company, entries: list[CompanyKnowledge], caplog
    ) -> None:
        with caplog.at_level(logging.INFO):
            search_for_agent(
                db, company.id, "can I cancel?",
                settings=settings_for(shadow=True, log_queries=True), provider=PROVIDER,
            )
        assert any(
            "query=can I cancel?" in r.getMessage() for r in caplog.records
        )


class TestAgentIsUnaffected:
    def test_the_agent_tool_still_returns_only_display_fields(
        self, db: Session, company: Company, entries: list[CompanyKnowledge]
    ) -> None:
        from app.agent.context import AgentContext
        from app.agent.tools import search_company_knowledge

        context = AgentContext(
            company_id=company.id,
            conversation_id=uuid.uuid4(),
            quote_id=uuid.uuid4(),
        )
        results = search_company_knowledge(db, context, {"query": "cancellation policy"})
        assert results.results
        for entry in results.results:
            assert set(entry.model_dump()) == {"category", "title", "content"}


# ------------------------------------------------- manual entries in the index


class TestManualEntryIndexStaysInStep:
    def test_creating_an_entry_indexes_it(
        self, db: Session, company: Company
    ) -> None:
        knowledge_service.create_entry(
            db,
            company.id,
            KnowledgeEntryIn(
                category="policy",
                title="Cancellation policy",
                content="Cancel free up to 72 hours before your move date.",
                keywords="cancel, refund",
            ),
            provider=PROVIDER,
        )
        chunks = db.scalars(select(KnowledgeChunk)).all()
        assert chunks
        assert all(chunk.embedding is not None for chunk in chunks)
        assert any("72 hours" in chunk.content for chunk in chunks)

    def test_editing_the_text_replaces_the_chunks(
        self, db: Session, company: Company
    ) -> None:
        created = knowledge_service.create_entry(
            db, company.id,
            KnowledgeEntryIn(
                category="policy", title="Cancellation policy",
                content="Cancel free up to 72 hours before your move date.",
            ),
            provider=PROVIDER,
        )
        knowledge_service.update_entry(
            db, company.id, created.id,
            KnowledgeEntryPatch(content="Cancel free up to 48 hours before your move."),
            provider=PROVIDER,
        )
        contents = [c.content for c in db.scalars(select(KnowledgeChunk))]
        assert any("48 hours" in c for c in contents)
        assert not any("72 hours" in c for c in contents)

    def test_toggling_activity_costs_no_embeddings(
        self, db: Session, company: Company
    ) -> None:
        """A checkbox must not put an API call behind it."""
        provider = _CountingProvider()
        created = knowledge_service.create_entry(
            db, company.id,
            KnowledgeEntryIn(
                category="policy", title="Cancellation policy",
                content="Cancel free up to 72 hours before your move date.",
            ),
            provider=provider,
        )
        after_create = provider.calls
        knowledge_service.update_entry(
            db, company.id, created.id, KnowledgeEntryPatch(is_active=False),
            provider=provider,
        )
        assert provider.calls == after_create
        assert all(not chunk.is_active for chunk in db.scalars(select(KnowledgeChunk)))

        knowledge_service.update_entry(
            db, company.id, created.id, KnowledgeEntryPatch(is_active=True),
            provider=provider,
        )
        assert provider.calls == after_create
        assert all(chunk.is_active for chunk in db.scalars(select(KnowledgeChunk)))

    def test_deleting_an_entry_takes_its_chunks(
        self, db: Session, company: Company
    ) -> None:
        created = knowledge_service.create_entry(
            db, company.id,
            KnowledgeEntryIn(
                category="policy", title="Cancellation policy",
                content="Cancel free up to 72 hours before your move date.",
            ),
            provider=PROVIDER,
        )
        assert db.scalars(select(KnowledgeChunk)).all()
        knowledge_service.delete_entry(db, company.id, created.id)
        assert db.scalars(select(KnowledgeChunk)).all() == []

    def test_an_indexing_failure_never_loses_the_owners_text(
        self, db: Session, company: Company, monkeypatch
    ) -> None:
        """The index is derived data; company_knowledge is the source of truth."""
        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("index unavailable")

        monkeypatch.setattr(indexing, "index_knowledge_entry", explode)
        created = knowledge_service.create_entry(
            db, company.id,
            KnowledgeEntryIn(
                category="policy", title="Cancellation policy",
                content="Cancel free up to 72 hours before your move date.",
            ),
            provider=PROVIDER,
        )
        assert created.id is not None
        assert db.scalars(select(KnowledgeChunk)).all() == []


class TestBackfill:
    def test_it_indexes_entries_written_before_the_index_existed(
        self, db: Session, company: Company, knowledge: list[CompanyKnowledge]
    ) -> None:
        report = indexing.backfill_company(db, company.id, provider=PROVIDER)
        assert report.entries == len(knowledge)
        assert report.indexed == len(knowledge)
        assert report.skipped == 0
        assert report.chunks_written > 0
        assert report.complete

    def test_re_running_it_embeds_nothing(
        self, db: Session, company: Company, knowledge: list[CompanyKnowledge]
    ) -> None:
        """What an operator does when the first run was interrupted."""
        indexing.backfill_company(db, company.id, provider=PROVIDER)
        provider = _CountingProvider()
        second = indexing.backfill_company(db, company.id, provider=provider)
        assert second.skipped == second.entries
        assert second.indexed == 0
        assert provider.calls == 0
        assert second.complete

    def test_an_edited_entry_is_picked_up_on_the_next_run(
        self, db: Session, company: Company, knowledge: list[CompanyKnowledge]
    ) -> None:
        indexing.backfill_company(db, company.id, provider=PROVIDER)
        knowledge[0].content = "Completely rewritten policy text for the 2026 season."
        db.commit()
        report = indexing.backfill_company(db, company.id, provider=PROVIDER)
        assert report.indexed == 1
        assert report.skipped == report.entries - 1

    def test_entries_indexed_during_an_outage_are_repaired_not_skipped(
        self, db: Session, company: Company, knowledge: list[CompanyKnowledge]
    ) -> None:
        """Chunks exist but carry no vectors — exactly what a re-run must fix."""
        indexing.backfill_company(db, company.id, provider=_BrokenProvider())
        assert all(c.embedding is None for c in db.scalars(select(KnowledgeChunk)))

        report = indexing.backfill_company(db, company.id, provider=PROVIDER)
        assert report.skipped == 0
        assert report.indexed == report.entries
        assert all(c.embedding is not None for c in db.scalars(select(KnowledgeChunk)))

    def test_force_re_indexes_regardless(
        self, db: Session, company: Company, knowledge: list[CompanyKnowledge]
    ) -> None:
        indexing.backfill_company(db, company.id, provider=PROVIDER)
        report = indexing.backfill_company(db, company.id, provider=PROVIDER, force=True)
        assert report.indexed == report.entries
        assert report.skipped == 0

    def test_it_never_reaches_another_tenant(
        self, db: Session, company: Company, knowledge: list[CompanyKnowledge]
    ) -> None:
        other = Company(name="Bravo", slug="bravo", email="ops@bravo.test", settings={})
        db.add(other)
        db.commit()
        db.add(
            CompanyKnowledge(
                company_id=other.id, category="policy", title="Bravo cancellations",
                content="Bravo keeps the deposit inside 24 hours.", is_active=True,
            )
        )
        db.commit()

        indexing.backfill_company(db, company.id, provider=PROVIDER)
        chunks = db.scalars(select(KnowledgeChunk)).all()
        assert chunks
        assert all(chunk.company_id == company.id for chunk in chunks)

    def test_it_preserves_a_deactivated_entrys_invisibility(
        self, db: Session, company: Company, knowledge: list[CompanyKnowledge]
    ) -> None:
        knowledge[0].is_active = False
        db.commit()
        indexing.backfill_company(db, company.id, provider=PROVIDER)
        inactive = db.scalars(
            select(KnowledgeChunk).where(
                KnowledgeChunk.knowledge_entry_id == knowledge[0].id
            )
        ).all()
        assert inactive and all(not chunk.is_active for chunk in inactive)

    def test_it_does_not_accumulate_chunks_across_runs(
        self, db: Session, company: Company, knowledge: list[CompanyKnowledge]
    ) -> None:
        indexing.backfill_company(db, company.id, provider=PROVIDER)
        first = len(db.scalars(select(KnowledgeChunk)).all())
        indexing.backfill_company(db, company.id, provider=PROVIDER, force=True)
        assert len(db.scalars(select(KnowledgeChunk)).all()) == first
