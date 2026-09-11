"""Historical move intelligence: import parsing, validation, and similarity.

A pure package like :mod:`app.pricing` — no database, no HTTP, no model. Everything here
is a function of its inputs, which is what makes messy-data behaviour testable without
fixtures and makes similarity results reproducible.
"""

from app.history.fields import (
    CANONICAL_FIELDS,
    FIELDS_BY_NAME,
    OUTCOME_FIELDS,
    REQUIRED_FIELDS,
    SENSITIVE_FIELDS,
    CanonicalField,
    FieldKind,
)
from app.history.parsing import (
    AMBIGUOUS,
    DateOrder,
    DecimalStyle,
    ParseError,
    detect_date_order,
    detect_decimal_style,
    parse_bool,
    parse_date,
    parse_money_cents,
)

__all__ = [
    "AMBIGUOUS",
    "CANONICAL_FIELDS",
    "FIELDS_BY_NAME",
    "OUTCOME_FIELDS",
    "REQUIRED_FIELDS",
    "SENSITIVE_FIELDS",
    "CanonicalField",
    "DateOrder",
    "DecimalStyle",
    "FieldKind",
    "ParseError",
    "detect_date_order",
    "detect_decimal_style",
    "parse_bool",
    "parse_date",
    "parse_money_cents",
]
