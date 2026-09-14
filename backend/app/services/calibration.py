"""Training, storing, and applying per-company calibration.

Three rules hold everywhere in this module:

**Strictly per company.** Every query filters ``company_id`` first. A model belongs to one
tenant and is meaningless anywhere else; there is no pooled model and no path by which
one company's history could reach another's prices.

**Shadow by default.** Training produces a model in ``shadow`` status. Nothing promotes
itself. Until a human activates one and the company's mode says ``active``, calibration
is computed, recorded, and ignored.

**Never fatal.** Recording a calibration must not be able to break quote creation. If
anything here raises, the quote stands on the rule engine's own number.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models import (
    CalibrationModelRow,
    CalibrationStatus,
    Company,
    Job,
    Quote,
    QuoteCalibration,
)
from app.pricing.backtest import rolling_origin_backtest, shuffled_label_control
from app.pricing.baseline import Excluded, TrainingRow, training_row
from app.pricing.calibration import (
    CalibratedEstimate,
    CalibrationLayer,
    CalibrationModel,
    build_model,
    clamp_cap_for,
    load_model,
)
from app.pricing.config import PricingConfig
from app.pricing.engine import Estimate
from app.pricing.spec import MoveSpec
from app.providers.distance import DistanceProvider
from app.services import geo
from app.services import pricing as pricing_service

logger = get_logger(__name__)

#: How a company's calibration behaves. Stored in ``company.settings``.
MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ACTIVE = "active"

#: Shadow is the default because Step 6B ships shadow-only: calibrations are computed and
#: recorded so they can be evaluated against real outcomes, and never shown to a customer.
DEFAULT_MODE = MODE_SHADOW

DEFAULT_ALGORITHM = "segmented_home_size"

_layer = CalibrationLayer()


def calibration_mode(company: Company) -> str:
    mode = str((company.settings or {}).get("calibration_mode", DEFAULT_MODE)).lower()
    return mode if mode in {MODE_OFF, MODE_SHADOW, MODE_ACTIVE} else DEFAULT_MODE


# --------------------------------------------------------------------------- training


@dataclass(frozen=True)
class TrainingSet:
    """Usable rows plus an honest account of everything left out."""

    rows: list[TrainingRow]
    excluded: list[Excluded]

    @property
    def exclusion_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.excluded:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return counts

    @property
    def fingerprint(self) -> str:
        """Identifies the exact rows fitted, so two models can be told apart."""
        payload = sorted(
            f"{r.job_id}:{r.actual_hours}:{round(r.base_hours, 4)}" for r in self.rows
        )
        return hashlib.sha256("|".join(payload).encode()).hexdigest()


def build_training_set(
    db: Session,
    company_id: uuid.UUID,
    *,
    distance_provider: DistanceProvider | None = None,
) -> TrainingSet:
    """Recompute our engine's baseline for every historical move of one company.

    Imported rows are baselined with *our* engine, never with the ``quoted_hours`` their
    old system recorded — training against someone else's estimate would teach us their
    bias instead of our own error.
    """
    # Seeds defaults on first use, exactly as quoting does. Reading the config directly
    # would make a company with history but no saved config look like a company with no
    # eligible history, which is a different and much more confusing answer.
    config_row = pricing_service.ensure_active_config(db, company_id)
    config = PricingConfig.model_validate(config_row.config)

    jobs = list(db.scalars(select(Job).where(Job.company_id == company_id)))

    # One batched pass for every distinct ZIP pair that needs a derived distance.
    derived: dict[geo.ZipPair, float | None] = {}
    if distance_provider is not None:
        derived = geo.distances_for_jobs(db, jobs, distance_provider)

    rows: list[TrainingRow] = []
    excluded: list[Excluded] = []
    for job in jobs:
        pair = geo.pair_for_job(job)
        distance = job.distance_miles
        if distance is None and pair is not None:
            distance = derived.get(pair)
        result = training_row(job, config, distance_miles=distance)
        (rows if isinstance(result, TrainingRow) else excluded).append(result)  # type: ignore[arg-type]

    return TrainingSet(rows=rows, excluded=excluded)


def train(
    db: Session,
    company_id: uuid.UUID,
    *,
    algorithm: str = DEFAULT_ALGORITHM,
    distance_provider: DistanceProvider | None = None,
) -> CalibrationModelRow:
    """Fit a model, backtest it, and store it in ``shadow`` status.

    The backtest is run here rather than on demand so that the numbers attached to a
    model are the ones produced by the data it was actually fitted on.
    """
    training = build_training_set(db, company_id, distance_provider=distance_provider)
    if not training.rows:
        raise ValidationError(
            "No historical moves are eligible for calibration yet. "
            f"Excluded: {training.exclusion_counts or 'nothing to consider'}"
        )

    pairs = [(row.home_size, row.log_ratio) for row in training.rows]
    model = build_model(algorithm, pairs)

    def fit(subset: list[TrainingRow]) -> CalibrationModel:
        return build_model(algorithm, [(r.home_size, r.log_ratio) for r in subset])

    backtest = rolling_origin_backtest(
        training.rows, fit, algorithm=algorithm, clamp_cap=clamp_cap_for
    )
    control = shuffled_label_control(training.rows, fit, algorithm=algorithm)

    dates = sorted(row.move_date for row in training.rows)
    version = (
        db.scalar(
            select(func.max(CalibrationModelRow.version)).where(
                CalibrationModelRow.company_id == company_id
            )
        )
        or 0
    ) + 1

    row = CalibrationModelRow(
        company_id=company_id,
        algorithm=algorithm,
        version=version,
        status=CalibrationStatus.SHADOW,
        params=model.to_params(),
        metrics={
            "backtest": _backtest_json(backtest),
            # Recorded next to the real result: a control that also "improves" means the
            # evaluation is broken, and that must be visible, not buried in a log.
            "shuffled_control": _backtest_json(control),
            "excluded": training.exclusion_counts,
            "n_eligible": len(training.rows),
            "n_considered": len(training.rows) + len(training.excluded),
        },
        n_train=len(training.rows),
        training_window_start=dates[0],
        training_window_end=dates[-1],
        dataset_fingerprint=training.fingerprint,
        trained_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "Calibration %s v%d trained for company %s on %d moves (excluded %s)",
        algorithm,
        version,
        company_id,
        len(training.rows),
        training.exclusion_counts,
    )
    return row


def _backtest_json(result: object) -> dict[str, object]:
    return json.loads(json.dumps(result, default=lambda o: getattr(o, "__dict__", str(o))))


# --------------------------------------------------------------------------- lifecycle


def list_models(db: Session, company_id: uuid.UUID) -> list[CalibrationModelRow]:
    return list(
        db.scalars(
            select(CalibrationModelRow)
            .where(CalibrationModelRow.company_id == company_id)
            .order_by(CalibrationModelRow.created_at.desc())
        )
    )


def active_model_row(db: Session, company_id: uuid.UUID) -> CalibrationModelRow | None:
    return db.scalar(
        select(CalibrationModelRow).where(
            CalibrationModelRow.company_id == company_id,
            CalibrationModelRow.status == CalibrationStatus.ACTIVE,
        )
    )


def latest_shadow_row(db: Session, company_id: uuid.UUID) -> CalibrationModelRow | None:
    return db.scalar(
        select(CalibrationModelRow)
        .where(
            CalibrationModelRow.company_id == company_id,
            CalibrationModelRow.status == CalibrationStatus.SHADOW,
        )
        .order_by(CalibrationModelRow.version.desc())
    )


def _owned(db: Session, company_id: uuid.UUID, model_id: uuid.UUID) -> CalibrationModelRow:
    """Tenant predicate inside the lookup, so another company's model reads as absent."""
    row = db.scalar(
        select(CalibrationModelRow).where(
            CalibrationModelRow.id == model_id, CalibrationModelRow.company_id == company_id
        )
    )
    if row is None:
        raise NotFoundError("Calibration model not found")
    return row


def activate(
    db: Session, company_id: uuid.UUID, model_id: uuid.UUID, *, user_id: uuid.UUID | None = None
) -> CalibrationModelRow:
    """Promote one model, retiring whatever was active. A deliberate human act."""
    row = _owned(db, company_id, model_id)
    if row.status is CalibrationStatus.RETIRED:
        raise ConflictError("A retired model cannot be reactivated; train a new one")

    current = active_model_row(db, company_id)
    if current is not None and current.id != row.id:
        current.status = CalibrationStatus.RETIRED
        current.retired_at = datetime.now(UTC)
        current.retired_reason = f"superseded by v{row.version}"

    row.status = CalibrationStatus.ACTIVE
    row.activated_at = datetime.now(UTC)
    row.activated_by_user_id = user_id
    db.commit()
    db.refresh(row)
    logger.info("Calibration v%d activated for company %s", row.version, company_id)
    return row


def retire(
    db: Session, company_id: uuid.UUID, model_id: uuid.UUID, *, reason: str = "manual"
) -> CalibrationModelRow:
    row = _owned(db, company_id, model_id)
    row.status = CalibrationStatus.RETIRED
    row.retired_at = datetime.now(UTC)
    row.retired_reason = reason[:200]
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- applying


def _model_for(
    db: Session, company: Company
) -> tuple[CalibrationModelRow | None, CalibrationModel | None]:
    """The model to evaluate for this company, and its rebuilt form.

    In shadow mode the newest shadow model is used so its behaviour can be observed;
    in active mode only an explicitly activated model counts.
    """
    mode = calibration_mode(company)
    if mode == MODE_OFF:
        return None, None
    row = active_model_row(db, company.id)
    if row is None and mode == MODE_SHADOW:
        row = latest_shadow_row(db, company.id)
    if row is None:
        return None, None
    try:
        return row, load_model(row.algorithm, row.params)
    except Exception:
        logger.exception("Stored calibration model could not be loaded; ignoring it")
        return row, None


def evaluate_for_quote(
    db: Session, company: Company, base: Estimate, spec: MoveSpec, config: PricingConfig
) -> tuple[CalibratedEstimate, CalibrationModelRow | None, bool]:
    """Compute the calibrated counterpart and say whether it may be applied.

    Returns ``(result, model_row, apply)``. ``apply`` is true only when the company is in
    ``active`` mode **and** an explicitly activated model produced a real adjustment.
    """
    row, model = _model_for(db, company)
    result = _layer.apply(base, spec, config, model)
    apply = (
        calibration_mode(company) == MODE_ACTIVE
        and row is not None
        and row.status is CalibrationStatus.ACTIVE
        and result.adjustment.applied
    )
    return result, row, apply


def record_for_quote(
    db: Session,
    *,
    company_id: uuid.UUID,
    quote_id: uuid.UUID,
    result: CalibratedEstimate,
    model_row: CalibrationModelRow | None,
    applied: bool,
) -> QuoteCalibration | None:
    """Persist what calibration decided for one quote.

    Best-effort by design: a failure to record the trace must never cost the customer
    their quote, so the exception is logged and swallowed.
    """
    adjustment = result.adjustment
    try:
        trace = QuoteCalibration(
            company_id=company_id,
            quote_id=quote_id,
            model_id=model_row.id if model_row else None,
            applied=applied,
            base_hours=result.base.estimated_hours,
            base_total_cents=result.base.total_cents,
            calibrated_hours=result.calibrated.estimated_hours,
            calibrated_total_cents=result.calibrated.total_cents,
            factor=adjustment.factor,
            raw_factor=adjustment.raw_factor,
            clamped=adjustment.clamped,
            cap=adjustment.cap,
            support=adjustment.support,
            segment=adjustment.segment,
            fallback_reason=adjustment.reason,
        )
        db.add(trace)
        db.flush()
        return trace
    except Exception:
        logger.exception("Could not record calibration for quote %s", quote_id)
        db.rollback()
        return None


def trace_for_quote(
    db: Session, company_id: uuid.UUID, quote_id: uuid.UUID
) -> QuoteCalibration | None:
    return db.scalar(
        select(QuoteCalibration).where(
            QuoteCalibration.quote_id == quote_id, QuoteCalibration.company_id == company_id
        )
    )


def shadow_comparison(db: Session, company_id: uuid.UUID) -> dict[str, object]:
    """How shadow calibration has been behaving — the promotion evidence, so far."""
    traces = list(
        db.scalars(select(QuoteCalibration).where(QuoteCalibration.company_id == company_id))
    )
    adjusted = [t for t in traces if t.fallback_reason is None]
    clamped = [t for t in adjusted if t.clamped]
    quotes_seen = db.scalar(
        select(func.count()).select_from(Quote).where(Quote.company_id == company_id)
    )
    return {
        "quotes": quotes_seen or 0,
        "calibrations_recorded": len(traces),
        "adjusted": len(adjusted),
        "applied": sum(1 for t in traces if t.applied),
        "clamped": len(clamped),
        "clamp_rate_pct": round(len(clamped) / len(adjusted) * 100, 1) if adjusted else None,
        "median_factor": (
            round(sorted(t.factor for t in adjusted)[len(adjusted) // 2], 4) if adjusted else None
        ),
        "fallback_reasons": {
            reason: sum(1 for t in traces if t.fallback_reason == reason)
            for reason in {t.fallback_reason for t in traces if t.fallback_reason}
        },
    }
