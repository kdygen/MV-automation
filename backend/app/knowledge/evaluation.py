"""Measuring retrieval before trusting it.

Retrieval quality is not something to assert from a handful of convincing examples. This
computes the numbers on a labelled fixture: whether the right passage comes back, how
highly it ranks, and — the measure that actually protects customers — how often an
unrelated question is answered with a confident-looking passage anyway.

**False-positive rate is the metric that decides the threshold.** Recall can always be
bought by lowering the floor; what that buys with it is fabricated policy. A retrieval
system for company policy should prefer silence to a plausible wrong answer, so the
threshold is chosen at the point that keeps rejection near-perfect while holding recall,
not at the point that maximizes recall.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy.orm import Session

from app.providers.embeddings import EmbeddingProvider
from app.services.retrieval import hybrid_search


class CaseKind(StrEnum):
    PARAPHRASE = "paraphrase"  # same meaning, different words
    EXACT_TERM = "exact_term"  # COI, DOT — lexical must carry these
    IRRELEVANT = "irrelevant"  # must return NOTHING
    CROSS_TENANT = "cross_tenant"  # another company holds the perfect match


@dataclass(frozen=True)
class EvalCase:
    """One labelled question."""

    query: str
    kind: CaseKind
    #: Titles of the passages that would be a correct answer. Empty for irrelevant and
    #: cross-tenant cases, where the correct answer is no passage at all.
    expected_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalResult:
    min_similarity: float
    n_cases: int
    n_retrievable: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float
    #: Share of irrelevant questions that returned any passage. Lower is better; this is
    #: the fabrication risk expressed as a number.
    false_positive_rate: float
    #: Share of cross-tenant probes that returned another company's passage. Must be 0.
    cross_tenant_leak_rate: float
    n_irrelevant: int = 0
    n_cross_tenant: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return self.cross_tenant_leak_rate == 0.0


def _rank_of_expected(titles: list[str], expected: tuple[str, ...]) -> int | None:
    """1-based position of the first correct passage, or ``None`` if absent."""
    for position, title in enumerate(titles, start=1):
        if any(want.lower() in title.lower() for want in expected):
            return position
    return None


def evaluate(
    db: Session,
    company_id: uuid.UUID,
    cases: list[EvalCase],
    *,
    provider: EmbeddingProvider | None,
    min_similarity: float,
    max_chunks: int = 5,
    foreign_titles: frozenset[str] = frozenset(),
) -> EvalResult:
    """Run every case and compute the metrics at one threshold."""
    hits_at = {1: 0, 3: 0, 5: 0}
    reciprocal = 0.0
    retrievable = 0
    false_positives = 0
    leaks = 0
    irrelevant = 0
    cross_tenant = 0
    failures: list[str] = []

    for case in cases:
        evidence = hybrid_search(
            db,
            company_id,
            case.query,
            provider=provider,
            min_similarity=min_similarity,
            max_chunks=max_chunks,
        )
        titles = [item.title for item in evidence]

        if any(title in foreign_titles for title in titles):
            leaks += 1
            failures.append(f"LEAK {case.query!r} -> {titles}")

        if case.kind is CaseKind.IRRELEVANT:
            irrelevant += 1
            if evidence:
                false_positives += 1
                failures.append(f"FALSE POSITIVE {case.query!r} -> {titles[:2]}")
            continue

        if case.kind is CaseKind.CROSS_TENANT:
            cross_tenant += 1
            if evidence:
                false_positives += 1
                failures.append(f"CROSS-TENANT ANSWER {case.query!r} -> {titles[:2]}")
            continue

        retrievable += 1
        rank = _rank_of_expected(titles, case.expected_titles)
        if rank is None:
            failures.append(f"MISS {case.query!r} expected {case.expected_titles} got {titles}")
            continue
        reciprocal += 1 / rank
        for k in hits_at:
            if rank <= k:
                hits_at[k] += 1

    def share(count: int, total: int) -> float:
        return round(count / total, 4) if total else 0.0

    return EvalResult(
        min_similarity=min_similarity,
        n_cases=len(cases),
        n_retrievable=retrievable,
        recall_at_1=share(hits_at[1], retrievable),
        recall_at_3=share(hits_at[3], retrievable),
        recall_at_5=share(hits_at[5], retrievable),
        mrr=round(reciprocal / retrievable, 4) if retrievable else 0.0,
        false_positive_rate=share(false_positives, irrelevant + cross_tenant),
        cross_tenant_leak_rate=share(leaks, len(cases)),
        n_irrelevant=irrelevant,
        n_cross_tenant=cross_tenant,
        failures=failures,
    )


def sweep(
    db: Session,
    company_id: uuid.UUID,
    cases: list[EvalCase],
    *,
    provider: EmbeddingProvider | None,
    thresholds: list[float],
    max_chunks: int = 5,
    foreign_titles: frozenset[str] = frozenset(),
) -> list[EvalResult]:
    """Evaluate across candidate thresholds so one can be chosen on evidence."""
    return [
        evaluate(
            db,
            company_id,
            cases,
            provider=provider,
            min_similarity=threshold,
            max_chunks=max_chunks,
            foreign_titles=foreign_titles,
        )
        for threshold in thresholds
    ]


def recommend_threshold(
    results: list[EvalResult], *, max_false_positive_rate: float = 0.0
) -> EvalResult | None:
    """Pick the threshold with the best recall among those that stay safe.

    Safety first, recall second — and never the reverse. A configuration that answers one
    more paraphrase but also answers a question the company never addressed is a worse
    system, however much better its recall looks.
    """
    safe = [
        r
        for r in results
        if r.cross_tenant_leak_rate == 0.0 and r.false_positive_rate <= max_false_positive_rate
    ]
    if not safe:
        return None
    return max(safe, key=lambda r: (r.recall_at_5, r.recall_at_3, r.mrr, -r.min_similarity))
