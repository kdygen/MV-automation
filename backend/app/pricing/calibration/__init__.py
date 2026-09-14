"""Calibration: bounded, versioned correction of the rule engine's hour estimate.

Strictly separate from :mod:`app.pricing.rules`. The engine stays a pure function of
spec and config, and remains the answer whenever calibration is unavailable, untrusted,
or out of its depth. Nothing here ever touches price directly — it adjusts *labor hours*,
and money follows through the company's own configured rates.
"""

from app.pricing.calibration.layer import (
    ABSOLUTE_MAX_ADJUSTMENT,
    Adjustment,
    CalibratedEstimate,
    CalibrationLayer,
    clamp_cap_for,
)
from app.pricing.calibration.models import (
    MIN_COMPANY_MOVES,
    MIN_SEGMENT_SUPPORT,
    CalibrationFeatures,
    CalibrationModel,
    GlobalScalarModel,
    SegmentedModel,
    build_model,
    load_model,
)

__all__ = [
    "ABSOLUTE_MAX_ADJUSTMENT",
    "MIN_COMPANY_MOVES",
    "MIN_SEGMENT_SUPPORT",
    "Adjustment",
    "CalibratedEstimate",
    "CalibrationFeatures",
    "CalibrationLayer",
    "CalibrationModel",
    "GlobalScalarModel",
    "SegmentedModel",
    "build_model",
    "clamp_cap_for",
    "load_model",
]
