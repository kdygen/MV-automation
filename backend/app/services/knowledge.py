"""Company knowledge search — deterministic keyword matching, no embeddings.

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
from sqlalchemy.orm import Session

from app.models import CompanyKnowledge

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
