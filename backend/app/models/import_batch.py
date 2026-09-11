"""Import batch — the provenance anchor for every historical move we did not create.

One row per confirmed import. It is what makes an imported job traceable back to the
file, the column mapping, and the person who uploaded it, and what makes a bad import
revertible as a unit rather than row by row.

Batches are created only on **confirmation**. Previewing an upload writes nothing at
all, so an abandoned preview leaves no batch behind to clean up.

Nothing here is ever exposed to a customer: filenames and mappings are operational
detail about a company's own systems.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import GUID, JSONType


class ImportFormat(enum.StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class JobImportBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One confirmed historical-move import."""

    __tablename__ = "job_import_batches"
    __table_args__ = (
        Index("ix_job_import_batches_company_id_created_at", "company_id", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    #: Original filename, for the owner's benefit ("did I already upload 2023.xlsx?").
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[ImportFormat] = mapped_column(
        SAEnum(ImportFormat, native_enum=False, length=10, validate_strings=True), nullable=False
    )
    #: The canonical-field → source-header mapping actually applied. Stored so an import
    #: can be explained and reproduced months later, when nobody remembers the columns.
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    #: Settings the company had to choose explicitly because they were ambiguous —
    #: date order and decimal style. Recorded because getting either wrong silently
    #: corrupts every row, so the choice must be auditable.
    parse_options: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    row_count_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_count_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_count_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_count_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Who uploaded it. SET NULL rather than CASCADE: a departed employee's imports are
    #: still the company's data and must not vanish with their account.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Set when the batch's rows were removed. Kept rather than deleted so "we imported
    #: this and undid it" stays visible.
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<JobImportBatch {self.filename} imported={self.row_count_imported}>"
