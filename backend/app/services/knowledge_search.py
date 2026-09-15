"""The one place a policy question turns into evidence for the agent.

Everything that asks "what has this company published about X?" comes through
:func:`search_for_agent`, so there is exactly one answer to three questions that would
otherwise be settled differently in different call sites: which retriever serves, what
happens when the semantic one fails, and whether the other one runs for comparison.

    question ─▶ keyword (Step 3A) ──────────────┐
             └▶ hybrid (vector + lexical) ──────┴─▶ one serves, the other is compared

**Keyword serves today.** ``knowledge_retrieval_mode`` defaults to ``keyword``, which is
what production has always answered with. The hybrid path is implemented and tested so
that cutover is a configuration change whose behaviour is already known — not a code
change made under time pressure on the day.

**Failure falls back, emptiness does not.** If embeddings are unconfigured or the
provider errors, hybrid mode serves the keyword result and says so. But hybrid returning
*nothing* is a real answer, not a failure: the similarity gate declining to answer is the
entire point of having one, and quietly substituting keyword results there would hide
exactly the behaviour the shadow run is measuring.

**The model never sees any of this.** The comparison, the source, the scores and the
chunk ids stay on our side of the tool boundary; the agent receives the same three
display fields it has received since Step 3A.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.knowledge.comparison import Comparison, compare
from app.models import KnowledgeChunk
from app.providers.embeddings import EmbeddingProvider, resolve_embedding_provider
from app.services.knowledge import MAX_RESULTS, search_knowledge
from app.services.retrieval import hybrid_search

logger = get_logger(__name__)

Source = Literal["keyword", "hybrid", "keyword_fallback"]

#: Sentinel so ``provider=None`` can mean "no embeddings available" rather than
#: "resolve one for me" — the difference the fallback path turns on.
_UNSET: EmbeddingProvider = object()  # type: ignore[assignment]


@dataclass(frozen=True)
class Answer:
    """One retrieved answer, in the shape the agent tool has always published."""

    category: str
    title: str
    content: str


@dataclass(frozen=True)
class SearchOutcome:
    """What was served, by which retriever, and how the two compared."""

    answers: tuple[Answer, ...]
    source: Source
    #: ``None`` when only one retriever ran — the normal case with shadow mode off.
    comparison: Comparison | None = None


def search_for_agent(
    db: Session,
    company_id: uuid.UUID,
    query: str,
    *,
    settings: Settings | None = None,
    provider: EmbeddingProvider | None = _UNSET,
) -> SearchOutcome:
    """Answer one policy question, and optionally measure the retriever we do not serve.

    ``company_id`` is the tenant boundary and comes from the caller's server-built scope
    — :class:`~app.agent.context.AgentContext` for the agent — never from ``query``.
    """
    settings = settings if settings is not None else get_settings()
    if provider is _UNSET:
        provider = resolve_embedding_provider(settings)

    keyword = _keyword_answers(db, company_id, query)
    serve_hybrid = settings.knowledge_retrieval_mode == "hybrid"

    if not (serve_hybrid or settings.knowledge_shadow_enabled):
        return SearchOutcome(answers=keyword, source="keyword")

    hybrid = _hybrid_answers(db, company_id, query, settings=settings, provider=provider)

    comparison = compare(
        query,
        [answer.title for answer in keyword],
        None if hybrid is None else [answer.title for answer in hybrid],
    )
    _log(comparison, query, settings)

    if serve_hybrid and hybrid is not None:
        return SearchOutcome(answers=hybrid, source="hybrid", comparison=comparison)
    if serve_hybrid:
        logger.warning("Hybrid retrieval unavailable; serving keyword results")
        return SearchOutcome(
            answers=keyword, source="keyword_fallback", comparison=comparison
        )
    return SearchOutcome(answers=keyword, source="keyword", comparison=comparison)


def _keyword_answers(
    db: Session, company_id: uuid.UUID, query: str
) -> tuple[Answer, ...]:
    """Step 3A. Deterministic, offline, and the fallback for everything else here."""
    return tuple(
        Answer(category=match.category, title=match.title, content=match.content)
        for match in search_knowledge(db, company_id, query, limit=MAX_RESULTS)
    )


def _hybrid_answers(
    db: Session,
    company_id: uuid.UUID,
    query: str,
    *,
    settings: Settings,
    provider: EmbeddingProvider | None,
) -> tuple[Answer, ...] | None:
    """Vector + lexical retrieval, or ``None`` if it could not run at all.

    An empty tuple and ``None`` mean different things and must not be conflated: the
    first is the gate declining to answer, the second is the retriever being unavailable.
    Only the second falls back.
    """
    if not _has_index(db, company_id):
        # No chunks means nothing could be retrieved, and paying for an embedding of the
        # customer's question to learn that would be spending money to find out nothing.
        return ()
    try:
        evidence = hybrid_search(
            db,
            company_id,
            query,
            provider=provider,
            min_similarity=settings.retrieval_min_similarity,
            max_chunks=settings.retrieval_max_chunks,
        )
    except Exception:  # noqa: BLE001 - a shadow arm must never reach the customer
        logger.exception("Hybrid retrieval failed for company %s", company_id)
        return None
    return tuple(
        Answer(category=item.category, title=item.title, content=item.content)
        for item in evidence
    )


def _has_index(db: Session, company_id: uuid.UUID) -> bool:
    """Whether this company has anything retrievable. One indexed count, no embedding."""
    return bool(
        db.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.company_id == company_id,
                KnowledgeChunk.is_active.is_(True),
            )
        )
    )


def _log(comparison: Comparison, query: str, settings: Settings) -> None:
    """Emit one greppable line per comparison.

    Written as ``key=value`` pairs rather than prose so a shadow run can be aggregated
    straight out of the platform's log search, which is the only aggregation that is
    correct across several workers and restarts.
    """
    fields = comparison.as_log_fields()
    if settings.knowledge_shadow_log_queries:
        fields["query"] = query
    logger.info(
        "knowledge_shadow %s",
        " ".join(f"{key}={value}" for key, value in fields.items()),
    )
