"""Calibration model registry and per-quote calibration trace.

Two tables, and between them any quote is reproducible forever:

    quote.inputs_snapshot + quote.pricing_config_id + quote.engine_version
      + quote_calibrations.model_id -> calibration_models.params

``quote_calibrations`` is a separate table rather than columns on ``quotes`` for one
concrete reason: **shadow mode records calibrations that were never applied.** A row with
``applied = False`` is the whole point of the rollout plan, and it has no business
mutating the quote it describes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID, JSONType


class CalibrationStatus(enum.StrEnum):
    """A model is never born active. Promotion is a deliberate, recorded act."""

    SHADOW = "shadow"  # computed alongside quotes, never shown
    ACTIVE = "active"  # affects customer-visible prices
    RETIRED = "retired"  # superseded or demoted


class CalibrationModelRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One fitted, versioned calibration model belonging to exactly one company."""

    __tablename__ = "calibration_models"
    __table_args__ = (
        Index("ix_calibration_models_company_id_status", "company_id", "status"),
        Index("ix_calibration_models_company_id_created_at", "company_id", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    algorithm: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Monotonic per company, so "version 3" is unambiguous in conversation.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[CalibrationStatus] = mapped_column(
        SAEnum(CalibrationStatus, native_enum=False, length=20, validate_strings=True),
        default=CalibrationStatus.SHADOW,
        nullable=False,
    )

    #: The fitted model itself — for V1 this is literally a factor, or one per home size.
    #: Small, readable, and enough to rebuild the model exactly.
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    #: Out-of-sample backtest output at training time, including the bootstrap interval.
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    n_train: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    training_window_start: Mapped[Any | None] = mapped_column(Date, nullable=True)
    training_window_end: Mapped[Any | None] = mapped_column(Date, nullable=True)
    #: Hash of the exact training rows. Two models with the same fingerprint saw the same
    #: data, which is what makes "why did the factor change?" answerable.
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<CalibrationModel {self.algorithm} v{self.version} {self.status.value}>"


class QuoteCalibration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """What calibration decided for one quote — including deciding to do nothing.

    A row exists for every quote once calibration is switched on in any mode, so
    "calibration was considered and declined" is as visible as "calibration was applied".
    """

    __tablename__ = "quote_calibrations"
    __table_args__ = (
        UniqueConstraint("quote_id", name="uq_quote_calibrations_quote_id"),
        Index("ix_quote_calibrations_company_id_created_at", "company_id", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    #: NULL when no model existed; the reason column then says why.
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("calibration_models.id", ondelete="SET NULL"), nullable=True
    )
    #: False in shadow mode. The customer saw ``base_*``, not ``calibrated_*``.
    applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    base_hours: Mapped[float] = mapped_column(Float, nullable=False)
    base_total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    calibrated_hours: Mapped[float] = mapped_column(Float, nullable=False)
    calibrated_total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)

    factor: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    #: What the model wanted before clamping — the gap between the two is the evidence
    #: that a model is straining against its bounds.
    raw_factor: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    clamped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    support: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    segment: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Why nothing was applied: no_model, insufficient_history, model_error, …
    fallback_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<QuoteCalibration factor={self.factor:.3f} applied={self.applied}>"
