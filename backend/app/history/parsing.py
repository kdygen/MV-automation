"""Value parsers for messy historical exports.

Two of these parsers refuse to guess, and that refusal is the whole point.

``03/04/2025`` is March 4th to an American mover and April 3rd to a Canadian one. There
is no way to tell from the value, and a silently wrong date poisons every seasonal and
day-of-week signal we later derive. ``1.234`` is either a thousand euros or a dollar and
change; guessing wrong is a 1000x error in a column we intend to calibrate prices
against. So both are detected **per column** across all its values, and when the whole
column stays ambiguous the caller must state the format explicitly.

Everything else is parsed forgivingly — ``"Yes"``, ``"y"``, ``"1"`` and ``"x"`` all mean
true — because rejecting a row over a boolean spelling loses real evidence for nothing.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import date, datetime

#: Returned by the detectors when a column cannot be read without being told how.
AMBIGUOUS = "ambiguous"


class DateOrder(enum.StrEnum):
    """How to read a numeric date whose component order is not self-evident."""

    ISO = "iso"  # 2025-03-04, unambiguous
    MDY = "mdy"  # 03/04/2025 = March 4
    DMY = "dmy"  # 03/04/2025 = 4 March


class DecimalStyle(enum.StrEnum):
    DOT = "dot"  # 1,234.56
    COMMA = "comma"  # 1.234,56


class ParseError(ValueError):
    """A value could not be parsed. The message is shown to the importing user."""


_TRUE = frozenset({"true", "t", "yes", "y", "1", "x", "✓", "✔", "on"})
_FALSE = frozenset({"false", "f", "no", "n", "0", "", "-", "off"})

_MONTHS = {
    name: index
    for index, group in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in group
}

_ISO_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_NUMERIC_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})$")
_NAMED_RE = re.compile(r"^(\d{1,2})?\s*([a-z]{3,9})\.?\s*(\d{1,2})?,?\s*(\d{4})$")
_CURRENCY_RE = re.compile(r"[^0-9,.\-]")


def _clean(value: object) -> str:
    """Normalize a cell to a trimmed lowercase string. ``None`` becomes ``\"\"``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    return str(value).strip().lower()


def parse_bool(value: object, *, field: str = "value") -> bool | None:
    """Read a truthy/falsy cell. Blank means "not stated", not ``False``."""
    text = _clean(value)
    if text == "":
        return None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ParseError(f"{field} '{value}' is not a yes/no value")


def _two_digit_year(year: int) -> int:
    """Expand a two-digit year. Moving histories are recent, never 19th century."""
    return 2000 + year if year < 70 else 1900 + year


@dataclass(frozen=True)
class DateShape:
    """One numeric date's components, before we know which is the day."""

    first: int
    second: int
    year: int


def _numeric_shape(text: str) -> DateShape | None:
    match = _NUMERIC_RE.match(text)
    if match is None:
        return None
    first, second, year_raw = int(match.group(1)), int(match.group(2)), match.group(3)
    year = int(year_raw) if len(year_raw) == 4 else _two_digit_year(int(year_raw))
    return DateShape(first=first, second=second, year=year)


def detect_date_order(values: list[object]) -> DateOrder | str:
    """Infer how a whole column's dates are written.

    Scans every value rather than the first: one row with a day above 12 settles the
    column, and that row may be anywhere. Returns :data:`AMBIGUOUS` when nothing in the
    column disambiguates, which the caller must resolve by asking.
    """
    saw_numeric = False
    saw_unambiguous_named_or_iso = False

    for raw in values:
        # A spreadsheet that stores real dates has already resolved the order for us;
        # only text needs interpreting. Without this, every XLSX upload would ask the
        # company a question its own file had already answered.
        if isinstance(raw, date | datetime):
            saw_unambiguous_named_or_iso = True
            continue
        text = _clean(raw)
        if not text:
            continue
        if _ISO_RE.match(text) or _NAMED_RE.match(text):
            saw_unambiguous_named_or_iso = True
            continue
        shape = _numeric_shape(text)
        if shape is None:
            continue
        saw_numeric = True
        if shape.first > 12:
            return DateOrder.DMY
        if shape.second > 12:
            return DateOrder.MDY

    if saw_numeric:
        # Every numeric value had both components <= 12: genuinely undecidable.
        return AMBIGUOUS
    if saw_unambiguous_named_or_iso:
        return DateOrder.ISO
    return AMBIGUOUS


def parse_date(value: object, *, order: DateOrder, field: str = "move_date") -> date | None:
    """Parse one date cell using an already-decided column ``order``."""
    text = _clean(value)
    if text == "":
        return None

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    iso = _ISO_RE.match(text)
    if iso:
        return _build(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), field, value)

    named = _NAMED_RE.match(text)
    if named and named.group(2) in _MONTHS:
        day = named.group(1) or named.group(3)
        if day is None:
            raise ParseError(f"{field} '{value}' has no day")
        return _build(int(named.group(4)), _MONTHS[named.group(2)], int(day), field, value)

    shape = _numeric_shape(text)
    if shape is None:
        raise ParseError(f"{field} '{value}' is not a date we recognise")
    month, day = (
        (shape.first, shape.second) if order is DateOrder.MDY else (shape.second, shape.first)
    )
    if order is DateOrder.ISO:
        # An ISO-ordered column still carrying d/m/y values: prefer the reading that works.
        month, day = shape.first, shape.second
    return _build(shape.year, month, day, field, value)


def _build(year: int, month: int, day: int, field: str, original: object) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ParseError(f"{field} '{original}' is not a real date ({exc})") from exc


def detect_decimal_style(values: list[object]) -> DecimalStyle | str:
    """Infer a money column's decimal separator.

    A value containing both separators settles it: the *last* one is the decimal point.
    Failing that, a single separator followed by exactly three digits is undecidable
    (``1.234`` is 1234 or 1.234), so the column stays :data:`AMBIGUOUS` and must be
    stated rather than assumed.
    """
    for raw in values:
        text = _CURRENCY_RE.sub("", _clean(raw))
        if not text:
            continue
        has_dot, has_comma = "." in text, "," in text
        if has_dot and has_comma:
            return DecimalStyle.DOT if text.rfind(".") > text.rfind(",") else DecimalStyle.COMMA
        if has_dot and text.count(".") == 1 and len(text.split(".")[1]) != 3:
            return DecimalStyle.DOT
        if has_comma and text.count(",") == 1 and len(text.split(",")[1]) != 3:
            return DecimalStyle.COMMA
        if text.count(".") > 1:
            return DecimalStyle.COMMA  # 1.234.567 can only be grouping
        if text.count(",") > 1:
            return DecimalStyle.DOT

    if any(_CURRENCY_RE.sub("", _clean(v)) for v in values):
        # Digits present, but every value had an undecidable separator pattern.
        for raw in values:
            text = _CURRENCY_RE.sub("", _clean(raw))
            if text and ("." in text or "," in text):
                return AMBIGUOUS
        return DecimalStyle.DOT  # plain integers: no separator to misread
    return DecimalStyle.DOT


def parse_money_cents(
    value: object, *, style: DecimalStyle, field: str = "amount"
) -> int | None:
    """Parse a currency cell into integer cents using an already-decided ``style``."""
    text = _CURRENCY_RE.sub("", _clean(value))
    if text in {"", "-"}:
        return None

    group, decimal = (",", ".") if style is DecimalStyle.DOT else (".", ",")
    text = text.replace(group, "").replace(decimal, ".")
    try:
        amount = float(text)
    except ValueError as exc:
        raise ParseError(f"{field} '{value}' is not an amount") from exc
    if amount < 0:
        raise ParseError(f"{field} '{value}' cannot be negative")
    return round(amount * 100)


def parse_int(
    value: object, *, field: str, minimum: int | None = None, maximum: int | None = None
) -> int | None:
    text = _clean(value).replace(",", "")
    if text == "":
        return None
    try:
        # Tolerate "3.0" from spreadsheets that store every number as a float.
        number = int(float(text))
    except ValueError as exc:
        raise ParseError(f"{field} '{value}' is not a whole number") from exc
    _check_range(number, field, value, minimum, maximum)
    return number


def parse_float(
    value: object, *, field: str, minimum: float | None = None, maximum: float | None = None
) -> float | None:
    text = _clean(value).replace(",", "")
    if text == "":
        return None
    # "7h 30m" and "7:30" appear in real duration columns.
    clock = re.match(r"^(\d+):(\d{1,2})$", text)
    if clock:
        number = int(clock.group(1)) + int(clock.group(2)) / 60
    else:
        text = re.sub(r"(hrs?|hours?|h)$", "", text).strip()
        try:
            number = float(text)
        except ValueError as exc:
            raise ParseError(f"{field} '{value}' is not a number") from exc
    _check_range(number, field, value, minimum, maximum)
    return round(number, 4)


def _check_range(
    number: float, field: str, original: object, minimum: float | None, maximum: float | None
) -> None:
    if minimum is not None and number < minimum:
        raise ParseError(f"{field} '{original}' is below the minimum of {minimum}")
    if maximum is not None and number > maximum:
        raise ParseError(f"{field} '{original}' is above the maximum of {maximum}")


def parse_list(value: object, *, field: str = "items") -> list[str] | None:
    """Split a multi-value cell. Blank means "unknown", not "none"."""
    text = value.strip() if isinstance(value, str) else _clean(value)
    if not text:
        return None
    parts = [part.strip() for part in re.split(r"[;,|/]", text)]
    return [part for part in parts if part][:20]
