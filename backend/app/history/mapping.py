"""Suggesting which source column means which canonical field.

Suggestions are *proposals*, never decisions. The importer shows them and the company
confirms, because a wrong guess here silently mislabels an entire history — a "Total"
column mapped to estimated rather than final price would invert every calibration signal
we later derive from it.

Sensitive fields are never suggested at all. Street addresses only ever get mapped
because a person deliberately chose to, which is what makes them opt-in rather than
opt-out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.history.fields import CANONICAL_FIELDS, FIELDS_BY_NAME, CanonicalField
from app.history.parsing import (
    AMBIGUOUS,
    DateOrder,
    DecimalStyle,
    detect_date_order,
    detect_decimal_style,
)
from app.history.tabular import Sheet

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_header(header: str) -> str:
    """Fold a header to its comparable form: ``\"From ZIP.\"`` → ``\"from zip\"``."""
    return _NON_ALNUM.sub(" ", (header or "").lower()).strip()


@dataclass(frozen=True)
class Suggestion:
    """One proposed mapping, with why it was proposed."""

    field: str
    header: str
    #: ``exact`` (header matches a known spelling) or ``partial`` (contained in one).
    confidence: str


def suggest_mapping(headers: tuple[str, ...]) -> dict[str, str]:
    """Propose ``{canonical_field: source_header}`` for a file's headers.

    Exact matches win over partial ones, and each header is claimed by at most one
    field, so a single "date" column cannot be proposed for three date fields at once.
    """
    return {s.field: s.header for s in suggestions(headers)}


def suggestions(headers: tuple[str, ...]) -> list[Suggestion]:
    normalized = {header: normalize_header(header) for header in headers}
    found: list[Suggestion] = []
    claimed_headers: set[str] = set()
    claimed_fields: set[str] = set()

    # Two passes so an exact match always beats a partial one, whatever the column order.
    for confidence in ("exact", "partial"):
        for field in CANONICAL_FIELDS:
            if field.sensitive or field.name in claimed_fields:
                continue
            header = _match(field, normalized, claimed_headers, exact=confidence == "exact")
            if header is None:
                continue
            found.append(Suggestion(field=field.name, header=header, confidence=confidence))
            claimed_headers.add(header)
            claimed_fields.add(field.name)
    return found


def _match(
    field: CanonicalField,
    normalized: dict[str, str],
    claimed: set[str],
    *,
    exact: bool,
) -> str | None:
    candidates = {normalize_header(s) for s in (*field.synonyms, field.name, field.label)}
    for header, norm in normalized.items():
        if header in claimed or not norm:
            continue
        if exact:
            if norm in candidates:
                return header
        else:
            # Partial matching requires a real word-boundary containment; a bare
            # substring test would map "crew" onto "crew notes".
            for candidate in candidates:
                if len(candidate) >= 4 and (
                    norm.startswith(candidate + " ") or norm.endswith(" " + candidate)
                ):
                    return header
    return None


@dataclass(frozen=True)
class AmbiguityReport:
    """What the company must decide before an import can be trusted.

    Empty lists mean the file read itself unambiguously and no questions are needed.
    """

    date_order: DateOrder | None
    decimal_style: DecimalStyle | None
    date_ambiguous: bool
    money_ambiguous: bool
    ambiguous_money_fields: tuple[str, ...] = ()

    @property
    def needs_input(self) -> bool:
        return self.date_ambiguous or self.money_ambiguous


def inspect_ambiguity(
    sheet: Sheet,
    mapping: dict[str, str],
    *,
    date_order: DateOrder | None = None,
    decimal_style: DecimalStyle | None = None,
) -> AmbiguityReport:
    """Decide how this file's dates and money must be read, or say we cannot.

    Detection scans whole columns, not sample rows: the one value that settles a date
    column's order may be row 900. An explicitly supplied option always wins — that is
    the company answering the question.
    """
    resolved_date: DateOrder | None = date_order
    date_ambiguous = False
    if resolved_date is None:
        date_header = mapping.get("move_date")
        if date_header:
            detected = detect_date_order(sheet.column(date_header))
            if detected == AMBIGUOUS:
                date_ambiguous = True
            else:
                resolved_date = DateOrder(detected)

    resolved_style: DecimalStyle | None = decimal_style
    money_ambiguous = False
    unresolved: list[str] = []
    if resolved_style is None:
        # One style for the whole file: a single export does not mix conventions, and
        # deciding per column would let two money columns disagree.
        for field_name, header in mapping.items():
            spec = FIELDS_BY_NAME.get(field_name)
            if spec is None or spec.kind.value != "money":
                continue
            detected = detect_decimal_style(sheet.column(header))
            if detected == AMBIGUOUS:
                unresolved.append(field_name)
            elif resolved_style is None:
                resolved_style = DecimalStyle(detected)
        money_ambiguous = bool(unresolved) and resolved_style is None

    return AmbiguityReport(
        date_order=resolved_date,
        decimal_style=resolved_style,
        date_ambiguous=date_ambiguous,
        money_ambiguous=money_ambiguous,
        ambiguous_money_fields=tuple(unresolved),
    )
