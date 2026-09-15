"""Comparing two retrievers over the same question, without serving either one twice.

Shadow mode exists to answer one question before cutover: *would the hybrid retriever
have answered this differently, and would that have been better?* The first half is
mechanical and lives here. The second half is a judgement call an operator makes by
reading the disagreements, which is why this module classifies rather than scores.

Two deliberate choices:

**Titles, not chunk ids.** The two retrievers return different units — Step 3A returns
whole knowledge entries, hybrid returns passages, and one entry can produce several
passages. Comparing ids would report disagreement on every single query. The title is
what the agent shows the customer and what a person recognises, so it is the unit on
which "the same answer" actually means something.

**No customer text.** A comparison carries counts, a verdict, and a hash of the query —
never the question a customer typed. Whether the text itself is logged is a separate,
explicit setting an operator turns on, because it is the one field here that is personal
data.
"""

from __future__ import annotations

import enum
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

#: How many distinct titles to compare from each side. Matched to Step 3A's ``MAX_RESULTS``
#: so the comparison is like-for-like: hybrid may retrieve five passages, but the
#: question is whether the *answers* agree, not how finely each side sliced them.
COMPARE_DEPTH = 3

_WHITESPACE = re.compile(r"\s+")


class Agreement(enum.StrEnum):
    """How two retrievers' answers relate. Ordered from best to worst outcome."""

    BOTH_EMPTY = "both_empty"  # both declined — agreement, and usually correct
    IDENTICAL = "identical"  # same titles, same order
    SAME_SET = "same_set"  # same titles, different order
    OVERLAP = "overlap"  # partial agreement
    DISJOINT = "disjoint"  # both answered, nothing in common — investigate
    KEYWORD_ONLY = "keyword_only"  # hybrid found nothing: a recall regression
    HYBRID_ONLY = "hybrid_only"  # hybrid found something keyword missed: the upside
    HYBRID_FAILED = "hybrid_failed"  # the shadow arm errored; nothing to conclude


@dataclass(frozen=True)
class Comparison:
    """One query, both retrievers, one verdict."""

    agreement: Agreement
    keyword_count: int
    hybrid_count: int
    #: Whether both sides led with the same answer. The single most useful number here:
    #: the agent quotes the top result far more often than the rest.
    top1_match: bool
    overlap: int
    jaccard: float
    #: Stable across repeats of the same question, so recurring disagreements can be
    #: counted without storing what anyone asked.
    query_fingerprint: str
    query_length: int

    @property
    def disagrees(self) -> bool:
        return self.agreement in {
            Agreement.DISJOINT,
            Agreement.KEYWORD_ONLY,
            Agreement.HYBRID_ONLY,
        }

    def as_log_fields(self) -> dict[str, object]:
        """Flat, greppable fields. Deliberately contains no customer text."""
        return {
            "agreement": str(self.agreement),
            "keyword_count": self.keyword_count,
            "hybrid_count": self.hybrid_count,
            "top1_match": self.top1_match,
            "overlap": self.overlap,
            "jaccard": round(self.jaccard, 3),
            "query_fingerprint": self.query_fingerprint,
            "query_length": self.query_length,
        }


def fingerprint(query: str) -> str:
    """A short, stable digest of a normalized question.

    Truncated to 12 hex characters: enough to group repeats of the same question across
    a shadow run, far too little to attack the original text with, and not reversible in
    any case since it is only ever used for counting.
    """
    normalized = _WHITESPACE.sub(" ", (query or "").strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def distinct_titles(titles: Sequence[str], *, depth: int = COMPARE_DEPTH) -> tuple[str, ...]:
    """The first ``depth`` distinct titles, in order, matched case-insensitively."""
    seen: set[str] = set()
    kept: list[str] = []
    for title in titles:
        key = title.strip().lower()
        if key and key not in seen:
            seen.add(key)
            kept.append(title)
        if len(kept) == depth:
            break
    return tuple(kept)


def compare(
    query: str, keyword: Sequence[str], hybrid: Sequence[str] | None
) -> Comparison:
    """Classify one query's two answers.

    ``hybrid is None`` means the shadow arm failed, which is reported as its own verdict
    rather than folded into "found nothing" — an outage and a genuine miss are different
    findings, and averaging them together would hide both.
    """
    left = distinct_titles(keyword)
    shared_fields = {
        "query_fingerprint": fingerprint(query),
        "query_length": len(query or ""),
    }
    if hybrid is None:
        return Comparison(
            agreement=Agreement.HYBRID_FAILED,
            keyword_count=len(left),
            hybrid_count=0,
            top1_match=False,
            overlap=0,
            jaccard=0.0,
            **shared_fields,  # type: ignore[arg-type]
        )

    right = distinct_titles(hybrid)
    left_keys = {title.strip().lower() for title in left}
    right_keys = {title.strip().lower() for title in right}
    shared = left_keys & right_keys
    union = left_keys | right_keys

    return Comparison(
        agreement=_verdict(left, right, left_keys, right_keys, shared),
        keyword_count=len(left),
        hybrid_count=len(right),
        top1_match=bool(left and right and left[0].strip().lower() == right[0].strip().lower()),
        overlap=len(shared),
        jaccard=len(shared) / len(union) if union else 1.0,
        **shared_fields,  # type: ignore[arg-type]
    )


def _verdict(
    left: tuple[str, ...],
    right: tuple[str, ...],
    left_keys: set[str],
    right_keys: set[str],
    shared: set[str],
) -> Agreement:
    if not left and not right:
        return Agreement.BOTH_EMPTY
    if not right:
        return Agreement.KEYWORD_ONLY
    if not left:
        return Agreement.HYBRID_ONLY
    if left_keys == right_keys:
        order_matches = [t.strip().lower() for t in left] == [
            t.strip().lower() for t in right
        ]
        return Agreement.IDENTICAL if order_matches else Agreement.SAME_SET
    return Agreement.OVERLAP if shared else Agreement.DISJOINT


@dataclass
class ShadowStats:
    """Running tally of a shadow run, for the offline harness and for tests.

    Not used to aggregate production traffic: a Render service runs several workers and
    restarts freely, so an in-process counter there would undercount silently. Production
    aggregation is done over the log lines instead.
    """

    counts: dict[Agreement, int]
    total: int = 0
    top1_matches: int = 0

    @classmethod
    def empty(cls) -> ShadowStats:
        return cls(counts=dict.fromkeys(Agreement, 0))

    def record(self, comparison: Comparison) -> None:
        self.counts[comparison.agreement] += 1
        self.total += 1
        self.top1_matches += int(comparison.top1_match)

    @property
    def top1_agreement(self) -> float:
        return self.top1_matches / self.total if self.total else 0.0

    @property
    def disagreement_rate(self) -> float:
        disagreeing = sum(
            self.counts[a]
            for a in (Agreement.DISJOINT, Agreement.KEYWORD_ONLY, Agreement.HYBRID_ONLY)
        )
        return disagreeing / self.total if self.total else 0.0

    def summary(self) -> str:
        parts = [f"{a.value}={self.counts[a]}" for a in Agreement if self.counts[a]]
        return (
            f"queries={self.total} top1_agreement={self.top1_agreement:.2f} "
            f"disagreement={self.disagreement_rate:.2f} " + " ".join(parts)
        )
