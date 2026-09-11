"""Turning mapped source rows into validated canonical values, with a verdict each.

Every row gets one of four verdicts, and the distinction is what makes an import
trustworthy rather than merely successful:

``ok``          clean.
``warning``     imports, but something optional was unreadable and was dropped. A
                mistyped elevator column should not cost us a row's hours.
``error``       rejected. The row lacks a required field, or a *required* value could
                not be parsed. Importing it would put a guess in the evidence base.
``duplicate``   skipped. Already present, by the company's own job number or by content.

Nothing here touches the database. The same function produces the preview and the
confirmed import, so what the company approves is exactly what gets written.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date

from app.history.fields import (
    FIELDS_BY_NAME,
    FINGERPRINT_FIELDS,
    OUTCOME_FIELDS,
    REQUIRED_FIELDS,
    CanonicalField,
    FieldKind,
)
from app.history.parsing import (
    DateOrder,
    DecimalStyle,
    ParseError,
    parse_bool,
    parse_date,
    parse_float,
    parse_int,
    parse_list,
    parse_money_cents,
)
from app.history.tabular import Sheet

_ZIP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{2,9}$")
_WHITESPACE = re.compile(r"\s+")

#: Text we keep from a free-text column. Long enough for a real operational note,
#: bounded so one runaway cell cannot dominate a row.
MAX_TEXT = 2000


class RowStatus(enum.StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class RowIssue:
    field: str | None
    message: str


@dataclass(frozen=True)
class RowVerdict:
    """One source row's outcome. ``values`` is empty unless the row is importable."""

    row_number: int
    status: RowStatus
    values: dict[str, object]
    errors: tuple[RowIssue, ...] = ()
    warnings: tuple[RowIssue, ...] = ()
    fingerprint: str | None = None
    external_ref: str | None = None

    @property
    def importable(self) -> bool:
        return self.status in (RowStatus.OK, RowStatus.WARNING)


@dataclass(frozen=True)
class ParseOptions:
    """The two decisions that cannot be guessed, once made."""

    date_order: DateOrder = DateOrder.ISO
    decimal_style: DecimalStyle = DecimalStyle.DOT


def _text(value: object, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE.sub(" ", str(value)).strip()
    return cleaned[:limit] or None


def _parse_value(spec: CanonicalField, raw: object, options: ParseOptions) -> object:
    """Parse one cell according to its canonical field. Raises :class:`ParseError`."""
    match spec.kind:
        case FieldKind.DATE:
            return parse_date(raw, order=options.date_order, field=spec.label)
        case FieldKind.MONEY:
            return parse_money_cents(raw, style=options.decimal_style, field=spec.label)
        case FieldKind.INT:
            return parse_int(
                raw,
                field=spec.label,
                minimum=None if spec.minimum is None else int(spec.minimum),
                maximum=None if spec.maximum is None else int(spec.maximum),
            )
        case FieldKind.FLOAT:
            return parse_float(
                raw, field=spec.label, minimum=spec.minimum, maximum=spec.maximum
            )
        case FieldKind.BOOL:
            return parse_bool(raw, field=spec.label)
        case FieldKind.LIST:
            return parse_list(raw, field=spec.label)
        case FieldKind.ENUM:
            return _parse_enum(spec, raw)
        case FieldKind.STATE:
            text = _text(raw, 20)
            if text is None:
                return None
            code = text.upper().replace(".", "")
            if len(code) != 2 or not code.isalpha():
                raise ParseError(f"{spec.label} '{raw}' is not a two-letter state code")
            return code
        case FieldKind.ZIP:
            text = _text(raw, 12)
            if text is None:
                return None
            if not _ZIP_RE.match(text):
                raise ParseError(f"{spec.label} '{raw}' is not a postal code")
            return text.upper()
        case _:
            return _text(raw)


def _parse_enum(spec: CanonicalField, raw: object) -> object:
    text = _text(raw, 60)
    if text is None or spec.enum is None:
        return None
    key = text.lower()
    mapped = spec.value_synonyms.get(key, key)
    try:
        return spec.enum(mapped)
    except ValueError:
        valid = ", ".join(member.value for member in spec.enum)
        raise ParseError(f"{spec.label} '{raw}' is not one of: {valid}") from None


def _building_key(line1: object, zip_code: object) -> str | None:
    """Group repeat visits to one address without matching free text at query time."""
    street = _text(line1, 300)
    if not street:
        return None
    postcode = _text(zip_code, 12) or ""
    return f"{street.lower()}|{postcode.lower()}"[:320]


def fingerprint(values: dict[str, object]) -> str:
    """Content hash used as the duplicate key when an export has no job number.

    Built from the identifying facts of a move, not every column, so a company that
    re-exports with an extra notes column is still recognised as re-uploading the same
    history rather than doubling it.
    """
    payload = []
    for name in FINGERPRINT_FIELDS:
        value = values.get(name)
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, enum.Enum):
            value = value.value
        elif isinstance(value, float):
            value = round(value, 2)
        payload.append([name, value])
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def check_business_rules(
    values: dict[str, object], *, already_flagged: set[str | None] | None = None
) -> list[RowIssue]:
    """Rules that hold however the values arrived — import row or manual entry.

    Shared so a hand-typed move and an imported one cannot diverge: a single place
    decides what makes a historical move admissible evidence.
    """
    flagged = already_flagged or set()
    issues: list[RowIssue] = []

    for name in REQUIRED_FIELDS:
        if values.get(name) is None and name not in flagged:
            label = FIELDS_BY_NAME[name].label
            issues.append(RowIssue(name, f"{label} is required and is missing"))

    if not any(values.get(name) is not None for name in OUTCOME_FIELDS):
        issues.append(
            RowIssue(
                None,
                "A row needs at least actual hours or a final total — without one it "
                "records no outcome",
            )
        )

    move_date = values.get("move_date")
    if isinstance(move_date, date) and move_date > date.today():
        issues.append(RowIssue("move_date", "Move date is in the future"))
    return issues


def derive_building_keys(values: dict[str, object]) -> None:
    """Add ``*_building_key`` in place wherever a street address was supplied."""
    for side in ("origin", "destination"):
        key = _building_key(values.get(f"{side}_line1"), values.get(f"{side}_zip"))
        if key:
            values[f"{side}_building_key"] = key


def validate_row(
    row: dict[str, object],
    mapping: dict[str, str],
    options: ParseOptions,
    *,
    row_number: int,
) -> RowVerdict:
    """Parse and judge one source row. Unmapped columns are ignored entirely."""
    values: dict[str, object] = {}
    errors: list[RowIssue] = []
    warnings: list[RowIssue] = []

    for field_name, header in mapping.items():
        spec = FIELDS_BY_NAME.get(field_name)
        if spec is None:
            # An unknown canonical name in the mapping is a client bug, not row data.
            warnings.append(RowIssue(field_name, f"Unknown field '{field_name}' ignored"))
            continue
        try:
            parsed = _parse_value(spec, row.get(header), options)
        except ParseError as exc:
            critical = field_name in REQUIRED_FIELDS or field_name in OUTCOME_FIELDS
            (errors if critical else warnings).append(RowIssue(field_name, str(exc)))
            continue
        if parsed is not None:
            values[field_name] = parsed

    errors.extend(check_business_rules(values, already_flagged={e.field for e in errors}))
    derive_building_keys(values)

    if errors:
        return RowVerdict(
            row_number=row_number,
            status=RowStatus.ERROR,
            values={},
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    external_ref = values.pop("external_ref", None)
    return RowVerdict(
        row_number=row_number,
        status=RowStatus.WARNING if warnings else RowStatus.OK,
        values=values,
        warnings=tuple(warnings),
        fingerprint=fingerprint(values),
        external_ref=str(external_ref) if external_ref else None,
    )


def validate_sheet(
    sheet: Sheet,
    mapping: dict[str, str],
    options: ParseOptions,
    *,
    known_refs: frozenset[str] = frozenset(),
    known_fingerprints: frozenset[str] = frozenset(),
) -> list[RowVerdict]:
    """Judge a whole upload, marking duplicates against history **and within the file**.

    A single export often repeats a job across sheets or re-lists a rescheduled move;
    catching that here means the same upload cannot duplicate itself.
    """
    seen_refs = set(known_refs)
    seen_prints = set(known_fingerprints)
    verdicts: list[RowVerdict] = []

    for index, row in enumerate(sheet.rows, start=1):
        verdict = validate_row(row, mapping, options, row_number=index)
        if verdict.importable:
            # A job number, when present, is a stronger identity than content: two real
            # moves can look identical, but a company does not reuse a job number.
            if verdict.external_ref is not None:
                duplicate = verdict.external_ref in seen_refs
            else:
                duplicate = (
                    verdict.fingerprint is not None and verdict.fingerprint in seen_prints
                )
            if duplicate:
                verdict = RowVerdict(
                    row_number=verdict.row_number,
                    status=RowStatus.DUPLICATE,
                    values={},
                    warnings=(RowIssue(None, "Already imported — skipped"),),
                    fingerprint=verdict.fingerprint,
                    external_ref=verdict.external_ref,
                )
            else:
                if verdict.external_ref:
                    seen_refs.add(verdict.external_ref)
                if verdict.fingerprint:
                    seen_prints.add(verdict.fingerprint)
        verdicts.append(verdict)
    return verdicts
