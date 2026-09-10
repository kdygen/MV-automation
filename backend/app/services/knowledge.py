"""Company knowledge: search for the agent, CRUD for the dashboard.

Both live here because they are two views of one table and share its tenant rule:
``company_id`` is always passed in by the caller from server-owned identity — the
authenticated user for dashboard writes, :class:`~app.agent.context.AgentContext` for
agent reads — and never derived from client input.

Search — deterministic keyword matching, no embeddings.

Answers questions the quote itself cannot ("do you provide a COI?", "what's your
cancellation policy?") by scoring a tenant's curated knowledge entries against the
customer's wording.

Why keyword scoring rather than vectors, at this stage:

* **Determinism.** The same question always returns the same entries, so behaviour is
  fully testable and a bad answer is traceable to a row, not to a similarity threshold.
* **Parity.** Scoring happens in Python over a tenant-scoped query, so SQLite (tests)
  and PostgreSQL (production) behave identically. PostgreSQL full-text search would
  give stemming and index-backed ranking, but would not run under SQLite and would add
  a ``tsvector`` column and GIN index to maintain — unjustified at tens of entries per
  tenant.
* **Cost and latency.** Embeddings would add an API call to every customer turn plus an
  ingestion pipeline. The ``keywords`` column buys most of the vocabulary flexibility
  ("COI" → "Certificate of Insurance") for free.

Revisit vectors when a tenant's entry count reaches the low hundreds, or when we can
measure misses that curated keywords cannot fix.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models import CompanyKnowledge
from app.schemas.knowledge import (
    KnowledgeEntryIn,
    KnowledgeEntryOut,
    KnowledgeEntryPatch,
    StarterTopicOut,
)

#: The category vocabulary offered in the dashboard. Free text at the database level —
#: this is a suggestion list, not a constraint — but a shared vocabulary keeps the
#: category weight in scoring meaningful and groups the dashboard sensibly.
KNOWLEDGE_CATEGORIES: tuple[str, ...] = (
    "insurance",
    "packing",
    "special_items",
    "policy",
    "access",
    "payment",
    "service_area",
    "scope",
)

#: Onboarding templates: the questions customers actually ask, with the curated search
#: vocabulary already filled in. Deliberately carries **no** ``content`` — every
#: business fact is the company's to write. Serving these from the backend keeps one
#: source of truth for the category vocabulary that scoring depends on.
STARTER_TOPICS: tuple[StarterTopicOut, ...] = (
    StarterTopicOut(
        category="insurance",
        title="Certificate of Insurance",
        prompt="Do you provide Certificates of Insurance (COIs)?",
        keywords="COI, certificate of insurance, building management, liability",
    ),
    StarterTopicOut(
        category="packing",
        title="Packing services and materials",
        prompt="Do you offer packing services and packing materials?",
        keywords="packing, boxes, cartons, tape, bubble wrap, supplies, materials",
    ),
    StarterTopicOut(
        category="special_items",
        title="Pianos and heavy items",
        prompt="Do you move pianos or other heavy/special items?",
        keywords="piano, safe, pool table, gym equipment, oversized, heavy, fragile",
    ),
    StarterTopicOut(
        category="policy",
        title="Cancellation policy",
        prompt="What is your cancellation policy?",
        keywords="cancel, cancellation, refund, deposit back, call off",
    ),
    StarterTopicOut(
        category="policy",
        title="Rescheduling policy",
        prompt="What is your rescheduling policy?",
        keywords="reschedule, change date, move date, postpone, delay",
    ),
    StarterTopicOut(
        category="access",
        title="Stairs",
        prompt="Are there extra fees for stairs?",
        keywords="stairs, flights, walk up, no elevator, third floor, extra fee",
    ),
    StarterTopicOut(
        category="access",
        title="Elevators and freight elevators",
        prompt="Do customers need to reserve an elevator or freight elevator?",
        keywords="elevator, lift, freight elevator, service elevator, reserve, booking",
    ),
    StarterTopicOut(
        category="service_area",
        title="Areas we serve",
        prompt="What areas do you serve?",
        keywords="areas, service area, coverage, how far, long distance, out of state",
    ),
    StarterTopicOut(
        category="payment",
        title="Deposits and payment",
        prompt="Do you require a deposit, and how can customers pay?",
        keywords="deposit, pay, payment, credit card, cash, e-transfer, invoice, tip",
    ),
    StarterTopicOut(
        category="policy",
        title="If the move takes longer than estimated",
        prompt="What happens if the move takes longer than estimated?",
        keywords="longer, overtime, over estimate, extra hours, go over, final price",
    ),
    StarterTopicOut(
        category="access",
        title="Parking and loading",
        prompt="What parking or loading access should customers arrange?",
        keywords="parking, loading dock, permit, driveway, street, truck access, distance to door",
    ),
    StarterTopicOut(
        category="scope",
        title="What is not included in a quote",
        prompt="What is not included in a standard quote?",
        keywords="not included, excluded, extra, additional charge, surcharge, disassembly",
    ),
)

#: How many entries the agent may see for one question. Enough to cover a question that
#: spans two policies, small enough that the model is not handed the whole knowledge
#: base to summarize.
MAX_RESULTS = 3

#: Shortest token worth matching; below this, tokens are noise ("a", "do", "is").
MIN_TOKEN_LENGTH = 3

#: Field weights. A hit in the title is the strongest signal that an entry is *about*
#: the thing asked; curated keywords rank just below it; a passing mention in the body
#: is the weakest.
_FIELD_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("title", 3),
    ("keywords", 2),
    ("category", 1),
    ("content", 1),
)

#: Words that appear in almost every customer question and would otherwise match
#: entries at random. Deliberately short: over-filtering loses real signal.
_STOPWORDS = frozenset(
    {
        "and", "any", "are", "ary", "but", "can", "did", "does", "for", "from",
        "get", "had", "has", "have", "how", "its", "may", "not", "our", "out",
        "she", "that", "the", "them", "then", "they", "this", "was", "were",
        "what", "when", "where", "which", "who", "will", "with", "would", "you",
        "your", "there", "their", "about", "into", "some", "such", "than",
        "these", "those", "here", "been", "being", "just", "much", "very",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class KnowledgeMatch:
    """One scored entry. ``score`` is internal ranking data and never leaves this layer."""

    category: str
    title: str
    content: str
    score: int


def _stem(token: str) -> str:
    """Fold trivial plurals so "movers"/"mover" and "boxes"/"box" match.

    Deliberately not a real stemmer: a Porter-style algorithm would add a dependency
    and surprising edge cases for a gain we cannot yet measure.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and short tokens, stem."""
    tokens = _WORD_RE.findall(text.lower())
    return {
        _stem(token)
        for token in tokens
        if len(token) >= MIN_TOKEN_LENGTH and token not in _STOPWORDS
    }


def _matches(query_token: str, field_tokens: set[str]) -> bool:
    """A field matches a query token on stem equality or a ≥4-character prefix.

    The prefix rule covers "cancel" → "cancellation" without letting three-letter
    fragments match half the knowledge base.
    """
    if query_token in field_tokens:
        return True
    if len(query_token) < 4:
        return False
    return any(field_token.startswith(query_token) for field_token in field_tokens)


def _score_entry(entry: CompanyKnowledge, query_tokens: set[str]) -> int:
    """Sum, per query token, the weight of the strongest field it matched.

    Scoring per *token* rather than per occurrence means an entry cannot win by
    repeating one word in its body; breadth of coverage beats repetition.
    """
    field_tokens = {
        field: tokenize(getattr(entry, field) or "") for field, _ in _FIELD_WEIGHTS
    }
    total = 0
    for query_token in query_tokens:
        best = 0
        for field, weight in _FIELD_WEIGHTS:
            if weight > best and _matches(query_token, field_tokens[field]):
                best = weight
        total += best
    return total


def search_knowledge(
    db: Session,
    company_id: uuid.UUID,
    query: str,
    *,
    limit: int = MAX_RESULTS,
) -> list[KnowledgeMatch]:
    """Return this company's best-matching active entries, highest score first.

    ``company_id`` is the tenant boundary and comes from the caller's server-built
    scope; it is never derived from ``query``. An empty or unmatched query returns an
    empty list rather than an arbitrary entry — the agent must be able to say it does
    not know.
    """
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    entries = db.scalars(
        select(CompanyKnowledge).where(
            CompanyKnowledge.company_id == company_id,
            CompanyKnowledge.is_active.is_(True),
        )
    ).all()

    scored = [
        KnowledgeMatch(
            category=entry.category,
            title=entry.title,
            content=entry.content,
            score=score,
        )
        for entry in entries
        if (score := _score_entry(entry, query_tokens)) > 0
    ]
    # Title is the tiebreaker purely so equal-scoring results have a stable order.
    scored.sort(key=lambda match: (-match.score, match.title))
    return scored[:limit]


# ---------------------------------------------------------------------------
# Dashboard CRUD
#
# Every function takes ``company_id`` as its second argument, supplied by the router
# from the authenticated user. There is no code path that reads a company from a
# request body or a path parameter.
# ---------------------------------------------------------------------------


def _normalize_keywords(value: str | None) -> str | None:
    """Store an omitted or cleared keywords field as NULL, never as an empty string.

    Pydantic has already trimmed it; this collapses ``""`` to ``None`` so "no keywords"
    has exactly one representation in the database.
    """
    return value or None


def _get_owned(db: Session, company_id: uuid.UUID, entry_id: uuid.UUID) -> CompanyKnowledge:
    """Load one entry, or raise ``NotFoundError``.

    The tenant predicate is part of the lookup rather than a check afterwards, so an
    entry belonging to another company is indistinguishable from one that does not
    exist. Returning 404 rather than 403 matters: 403 would confirm the id is real and
    turn this endpoint into an enumeration oracle.
    """
    entry = db.scalar(
        select(CompanyKnowledge).where(
            CompanyKnowledge.id == entry_id,
            CompanyKnowledge.company_id == company_id,
        )
    )
    if entry is None:
        raise NotFoundError("Knowledge entry not found")
    return entry


def _duplicate_title(
    db: Session,
    company_id: uuid.UUID,
    title: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    query = select(CompanyKnowledge.id).where(
        CompanyKnowledge.company_id == company_id,
        CompanyKnowledge.title == title,
    )
    if exclude_id is not None:
        query = query.where(CompanyKnowledge.id != exclude_id)
    return db.scalar(query) is not None


def _commit(db: Session, title: str) -> None:
    """Commit, translating the unique-title violation into a 409.

    The pre-checks give a clear message in the normal case; this is what actually holds
    under a race, since only the database can decide uniqueness atomically.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(f"An entry titled {title!r} already exists") from exc


def list_entries(db: Session, company_id: uuid.UUID) -> list[KnowledgeEntryOut]:
    """Every entry for this company, active and inactive, grouped-friendly ordered.

    Inactive entries are included deliberately: the dashboard is where an owner
    re-activates something, so hiding them would strand them.
    """
    entries = db.scalars(
        select(CompanyKnowledge)
        .where(CompanyKnowledge.company_id == company_id)
        .order_by(CompanyKnowledge.category, CompanyKnowledge.title)
    )
    return [KnowledgeEntryOut.model_validate(entry) for entry in entries]


def create_entry(
    db: Session, company_id: uuid.UUID, payload: KnowledgeEntryIn
) -> KnowledgeEntryOut:
    """Create one entry for this company. Title must be unique within the tenant."""
    if _duplicate_title(db, company_id, payload.title):
        raise ConflictError(f"An entry titled {payload.title!r} already exists")

    entry = CompanyKnowledge(
        company_id=company_id,
        category=payload.category,
        title=payload.title,
        content=payload.content,
        keywords=_normalize_keywords(payload.keywords),
        is_active=payload.is_active,
    )
    db.add(entry)
    _commit(db, payload.title)
    db.refresh(entry)
    return KnowledgeEntryOut.model_validate(entry)


def update_entry(
    db: Session,
    company_id: uuid.UUID,
    entry_id: uuid.UUID,
    patch: KnowledgeEntryPatch,
) -> KnowledgeEntryOut:
    """Apply a partial update. Unset fields are left alone; ``None`` clears keywords."""
    entry = _get_owned(db, company_id, entry_id)
    # exclude_unset distinguishes "not sent" from "sent as null" — the only way a
    # PATCH can both leave keywords alone and clear them.
    changes = patch.model_dump(exclude_unset=True)

    if "title" in changes and _duplicate_title(
        db, company_id, changes["title"], exclude_id=entry.id
    ):
        raise ConflictError(f"An entry titled {changes['title']!r} already exists")
    if "keywords" in changes:
        changes["keywords"] = _normalize_keywords(changes["keywords"])

    for field, value in changes.items():
        setattr(entry, field, value)

    _commit(db, entry.title)
    db.refresh(entry)
    return KnowledgeEntryOut.model_validate(entry)


def delete_entry(db: Session, company_id: uuid.UUID, entry_id: uuid.UUID) -> None:
    """Permanently remove one entry.

    Safe to hard-delete, unlike quotes or jobs: nothing references a knowledge row, and
    it carries no audit obligation. Deactivating (``is_active = false``) is the
    reversible option the dashboard puts first.
    """
    db.delete(_get_owned(db, company_id, entry_id))
    db.commit()


def list_starter_topics() -> list[StarterTopicOut]:
    """Onboarding templates. Static, tenant-independent, and answer-free."""
    return list(STARTER_TOPICS)
