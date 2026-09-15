"""Combining two rankings, then refusing most of what comes out.

Hybrid retrieval exists because company policy contains both kinds of language. "COI",
"DOT number" and "Form 4" are exact tokens where a lexical index wins outright; "what if
I back out three days early" is paraphrase where only a vector finds the passage. Running
one arm alone loses half the questions.

Fusion is **Reciprocal Rank Fusion** rather than a weighted blend of scores. Cosine
distance and ``ts_rank_cd`` live on incompatible scales, and normalizing them needs
constants that have to be re-tuned whenever either side changes. RRF uses only positions,
so it needs no tuning and is deterministic.

The gate matters more than the ranking. A vector search's top result is **never** empty —
something is always closest — so rank alone would return a confident-looking passage for
"do you move pianos?" at a company with no piano policy. That is exactly how a retrieval
system starts inventing company policy, so a result must clear an absolute similarity
floor or have genuinely matched lexically. Nothing else qualifies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

#: Standard RRF constant. Large enough that the top few ranks are not overwhelmingly
#: dominant, small enough that deep results cannot climb on agreement alone.
RRF_K = 60


@dataclass(frozen=True)
class Candidate:
    """One chunk proposed by at least one retrieval arm."""

    chunk_id: str
    content: str
    title: str
    category: str
    #: Cosine similarity, present only when the vector arm surfaced it.
    similarity: float | None = None
    #: 1-based positions in each arm; ``None`` means that arm did not return it.
    lexical_rank: int | None = None
    vector_rank: int | None = None
    score: float = 0.0
    #: Provenance for dedup and for the internal evidence record — never shown to a model.
    document_id: str | None = None
    knowledge_entry_id: str | None = None
    chunk_index: int = 0
    content_hash: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def lexical_hit(self) -> bool:
        return self.lexical_rank is not None


def reciprocal_rank_fusion(
    candidates: Sequence[Candidate], *, k: int = RRF_K
) -> list[Candidate]:
    """Score by summed reciprocal rank and order deterministically.

    Ties break on chunk id so two identical queries return byte-identical results — a
    property the evaluation harness depends on and customers notice.
    """
    scored = [
        replace(
            candidate,
            score=sum(
                1.0 / (k + rank)
                for rank in (candidate.lexical_rank, candidate.vector_rank)
                if rank is not None
            ),
        )
        for candidate in candidates
    ]
    return sorted(scored, key=lambda c: (-c.score, c.chunk_id))


def deduplicate(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Drop repeats of the same passage, keeping the best-ranked copy.

    Two things collide in practice: identical text appearing in two documents (a policy
    copied between them), and adjacent overlapping chunks from one document, where the
    overlap deliberately duplicates a clause. Both would otherwise spend the answer's
    limited slots restating one fact.
    """
    seen_hashes: set[str] = set()
    seen_adjacent: set[tuple[str, int]] = set()
    kept: list[Candidate] = []

    for candidate in candidates:
        if candidate.content_hash and candidate.content_hash in seen_hashes:
            continue
        if candidate.document_id is not None:
            neighbours = {
                (candidate.document_id, candidate.chunk_index + offset) for offset in (-1, 0, 1)
            }
            if neighbours & seen_adjacent:
                continue
            seen_adjacent.add((candidate.document_id, candidate.chunk_index))
        if candidate.content_hash:
            seen_hashes.add(candidate.content_hash)
        kept.append(candidate)
    return kept


def gate(
    candidates: Sequence[Candidate], *, min_similarity: float, max_chunks: int
) -> list[Candidate]:
    """Keep only what is actually evidence, and at most ``max_chunks`` of it.

    A candidate qualifies on either arm's own terms: a cosine similarity at or above the
    floor, or a real lexical match. A candidate that merely *ranked* — the inevitable
    nearest neighbour of an unrelated question — qualifies on neither and is dropped,
    leaving the agent with no evidence and its existing instruction to say so.
    """
    qualified = [
        candidate
        for candidate in candidates
        if candidate.lexical_hit
        or (candidate.similarity is not None and candidate.similarity >= min_similarity)
    ]
    return qualified[: max(max_chunks, 0)]


def merge_arms(lexical: Sequence[Candidate], vector: Sequence[Candidate]) -> list[Candidate]:
    """Combine the two arms' results into one candidate per chunk.

    Ranks are assigned here from each arm's own ordering, so neither arm needs to know
    the other exists.
    """
    merged: dict[str, Candidate] = {}

    for position, candidate in enumerate(lexical, start=1):
        merged[candidate.chunk_id] = replace(candidate, lexical_rank=position)

    for position, candidate in enumerate(vector, start=1):
        existing = merged.get(candidate.chunk_id)
        if existing is None:
            merged[candidate.chunk_id] = replace(candidate, vector_rank=position)
        else:
            merged[candidate.chunk_id] = replace(
                existing, vector_rank=position, similarity=candidate.similarity
            )
    return list(merged.values())
