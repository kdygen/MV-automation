"""Hybrid retrieval over a company's knowledge chunks.

    query ─┬─▶ lexical candidates ─┐
           └─▶ vector candidates  ─┴─▶ RRF ─▶ dedupe ─▶ gate ─▶ evidence

**The tenant predicate is the first clause of both queries**, before any ranking exists.
A better match belonging to another company is not a candidate that loses; it is never a
candidate. The same clause excludes inactive chunks, retired entries, and documents that
are not ``ready`` — so deactivating a policy removes it from answers immediately, without
anything downstream having to remember to filter.

PostgreSQL and SQLite differ only in **how candidates are found** — an HNSW cosine scan
versus Python cosine over the tenant's rows. Fusion, dedup and gating are the same pure
functions either way, so the logic most likely to be wrong is exercised identically on
both, and an opt-in parity test pins the two candidate paths to the same ordering.

Every ordering here breaks ties on ``content_hash`` rather than on the chunk's primary
key. A chunk id is generated fresh by each insert, so an id tiebreaker would reorder
equally-scoring passages whenever a document was re-indexed, and would order them
differently in PostgreSQL than in SQLite from the same data. The content hash is derived
from the passage text, so it is the same value in both databases and survives a
re-index — which is what makes "the same question returns the same answer" true rather
than usually true.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.vector import cosine_similarity
from app.knowledge.fusion import (
    Candidate,
    deduplicate,
    gate,
    merge_arms,
    reciprocal_rank_fusion,
)
from app.models import (
    CompanyKnowledge,
    DocumentStatus,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.providers.embeddings import EmbeddingError, EmbeddingProvider
from app.services.knowledge import tokenize

logger = get_logger(__name__)

#: How many candidates each arm contributes before fusion. Deeper than the final cap so
#: a passage that one arm ranks 12th can still win on agreement with the other.
ARM_LIMIT = 20

#: Guard on the SQLite path, which scores in Python. A tenant with more active chunks
#: than this needs PostgreSQL; the lexical arm still answers in the meantime.
MAX_PYTHON_SCAN = 5000

#: Decimal places a similarity is rounded to before ordering.
#:
#: pgvector stores ``float4`` and SQLite scores in Python ``float``, so the same passage
#: and the same query produce similarities that agree to about eight decimal places and
#: not further. Two passages that genuinely tie therefore differ by ~1e-9, which is
#: enough to order them one way in PostgreSQL and the other way in SQLite — and enough to
#: reorder them between two runs on the same database. Rounding first means a real tie is
#: recognised as a tie and broken by ``content_hash``, which is stable everywhere. Nothing
#: is lost: a 1e-9 difference in cosine similarity is not a ranking signal.
SIMILARITY_PRECISION = 6


@dataclass(frozen=True)
class Evidence:
    """One retrieved passage, plus why it was retrieved.

    The provenance fields are for the dashboard, the evaluation harness and the logs.
    They are deliberately *not* what the agent receives — see
    :func:`app.services.retrieval.to_tool_results`.
    """

    chunk_id: str
    title: str
    category: str
    content: str
    score: float
    similarity: float | None
    lexical_hit: bool
    document_id: str | None
    knowledge_entry_id: str | None


def _visible_chunks(company_id: uuid.UUID) -> Select[tuple[KnowledgeChunk]]:
    """Every chunk this company may retrieve, and nothing else.

    Built once and reused by both arms so the two can never disagree about what is
    visible — the failure that would let a retired policy surface through one path.
    """
    return (
        select(KnowledgeChunk)
        .outerjoin(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .outerjoin(
            CompanyKnowledge, KnowledgeChunk.knowledge_entry_id == CompanyKnowledge.id
        )
        .where(
            KnowledgeChunk.company_id == company_id,
            KnowledgeChunk.is_active.is_(True),
            or_(
                KnowledgeChunk.document_id.is_(None),
                (KnowledgeDocument.status == DocumentStatus.READY)
                & (KnowledgeDocument.deleted_at.is_(None)),
            ),
            or_(
                KnowledgeChunk.knowledge_entry_id.is_(None),
                CompanyKnowledge.is_active.is_(True),
            ),
        )
    )


def _to_candidate(chunk: KnowledgeChunk, *, similarity: float | None = None) -> Candidate:
    """Shape a stored chunk for ranking.

    ``title`` and ``category`` reproduce exactly what the Step 3A tool returned, so the
    agent sees the same two labels whichever retrieval path produced them.
    """
    metadata = chunk.chunk_metadata or {}
    return Candidate(
        chunk_id=str(chunk.id),
        content=chunk.content,
        title=chunk.heading or "Company policy",
        category=str(metadata.get("category") or "policy"),
        similarity=similarity,
        document_id=str(chunk.document_id) if chunk.document_id else None,
        knowledge_entry_id=(
            str(chunk.knowledge_entry_id) if chunk.knowledge_entry_id else None
        ),
        chunk_index=chunk.chunk_index,
        content_hash=chunk.content_hash,
    )


def _lexical_candidates(
    db: Session, company_id: uuid.UUID, query: str, *, limit: int = ARM_LIMIT
) -> list[Candidate]:
    """Exact-term arm.

    On PostgreSQL this is full-text search against the functional GIN index; the same
    expression is used in the query so the index applies. On SQLite it falls back to the
    Step 3A token scorer, which is retained precisely for this and for provider outages.
    """
    terms = tokenize(query)
    if not terms:
        return []

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        from sqlalchemy import func as sa_func

        tsv = sa_func.to_tsvector("english", KnowledgeChunk.content)
        tsq = sa_func.websearch_to_tsquery("english", query)
        statement = (
            _visible_chunks(company_id)
            .where(tsv.op("@@")(tsq))
            .order_by(sa_func.ts_rank_cd(tsv, tsq).desc(), KnowledgeChunk.content_hash)
            .limit(limit)
        )
        return [_to_candidate(chunk) for chunk in db.scalars(statement)]

    # SQLite: score in Python, requiring **every** query term to appear.
    #
    # Conjunctive on purpose, for two reasons. It matches PostgreSQL, where
    # ``websearch_to_tsquery`` ANDs bare terms — an OR here would make the two dialects
    # return different things from the same data. And it is what stops "do you sell car
    # insurance for boats?" matching the insurance policy on the single word "insurance",
    # which is the difference between a lexical hit meaning something and meaning nothing.
    #
    # Long paraphrased questions will match nothing here, which is correct: that is the
    # vector arm's job, and a weak lexical hit is worse than none.
    scored: list[tuple[int, KnowledgeChunk]] = []
    for chunk in db.scalars(_visible_chunks(company_id).limit(MAX_PYTHON_SCAN)):
        chunk_terms = tokenize(chunk.content)
        if terms <= chunk_terms:
            scored.append((len(terms), chunk))
    scored.sort(key=lambda row: (-row[0], row[1].content_hash))
    return [_to_candidate(chunk) for _, chunk in scored[:limit]]


def _vector_candidates(
    db: Session,
    company_id: uuid.UUID,
    query_vector: list[float],
    model: str,
    *,
    limit: int = ARM_LIMIT,
) -> list[Candidate]:
    """Semantic arm. Embeddings from different models are never compared."""
    base = _visible_chunks(company_id).where(
        KnowledgeChunk.embedding.is_not(None), KnowledgeChunk.embedding_model == model
    )

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        # The SQL ordering stays on the raw distance so the HNSW index can serve it; the
        # rows it returns are then re-ordered deterministically below.
        distance = KnowledgeChunk.embedding.cosine_distance(query_vector)
        rows = db.execute(
            base.add_columns(distance.label("distance"))
            .order_by(distance, KnowledgeChunk.content_hash)
            .limit(limit)
        ).all()
        scored = [(1.0 - float(distance), chunk) for chunk, distance in rows]
    else:
        scored = []
        for chunk in db.scalars(base.limit(MAX_PYTHON_SCAN)):
            stored = chunk.embedding
            if not stored:
                continue
            scored.append((cosine_similarity(query_vector, list(stored)), chunk))

    return [
        _to_candidate(chunk, similarity=similarity)
        for similarity, chunk in _rank(scored)[:limit]
    ]


def _rank(
    scored: list[tuple[float, KnowledgeChunk]]
) -> list[tuple[float, KnowledgeChunk]]:
    """Order by similarity, breaking genuine ties on content rather than on float noise."""
    return sorted(
        scored,
        key=lambda row: (-round(row[0], SIMILARITY_PRECISION), row[1].content_hash),
    )


def hybrid_search(
    db: Session,
    company_id: uuid.UUID,
    query: str,
    *,
    provider: EmbeddingProvider | None = None,
    min_similarity: float,
    max_chunks: int,
) -> list[Evidence]:
    """Retrieve evidence for one question, or return nothing at all.

    Returning nothing is a normal, frequent outcome and the whole point of the gate: the
    agent already knows to say the company has not published an answer, and that is a far
    better response than the nearest unrelated passage presented as policy.
    """
    lexical = _lexical_candidates(db, company_id, query)

    vector: list[Candidate] = []
    if provider is not None:
        try:
            result = provider.embed([query])
            if result.vectors:
                vector = _vector_candidates(db, company_id, result.vectors[0], result.model)
        except EmbeddingError:
            # Degrade to lexical-only rather than fail the customer's question.
            logger.warning("Query embedding failed; retrieving lexically only")

    fused = reciprocal_rank_fusion(merge_arms(lexical, vector))
    kept = gate(
        deduplicate(fused), min_similarity=min_similarity, max_chunks=max_chunks
    )

    return [
        Evidence(
            chunk_id=candidate.chunk_id,
            title=candidate.title,
            category=candidate.category,
            content=candidate.content,
            score=candidate.score,
            similarity=candidate.similarity,
            lexical_hit=candidate.lexical_hit,
            document_id=candidate.document_id,
            knowledge_entry_id=candidate.knowledge_entry_id,
        )
        for candidate in kept
    ]


def to_tool_results(evidence: list[Evidence]) -> list[dict[str, str]]:
    """Shape evidence into the agent tool's existing output contract.

    Identical to what Step 3A returns: ``category``, ``title``, ``content`` and nothing
    else. Scores, chunk ids, document ids and similarity stay on our side of the line —
    the model has never needed them and must never be able to name one.
    """
    return [
        {"category": item.category, "title": item.title, "content": item.content}
        for item in evidence
    ]
