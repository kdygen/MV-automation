"""Historical move service: inspect, preview, import, revert, and hand entry.

The tenant rule, stated once: ``company_id`` is always an argument supplied by the router
from the authenticated user. It is never read from a file, a body, or a query — and
because the canonical registry has no ``company_id`` field, there is no code path that
could read one even by mistake.

The preview/confirm split is the same shape as Step 4's date change: previewing parses
and judges without opening a transaction, and confirming re-validates from scratch
rather than trusting what a preview concluded. That matters here because "what is already
in your history" changes between the two calls.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.history.fields import (
    CANONICAL_FIELDS,
    FIELDS_BY_NAME,
    OUTCOME_FIELDS,
    REQUIRED_FIELDS,
)
from app.history.mapping import inspect_ambiguity, suggestions
from app.history.parsing import DateOrder, DecimalStyle
from app.history.rows import (
    ParseOptions,
    RowStatus,
    RowVerdict,
    check_business_rules,
    derive_building_keys,
    fingerprint,
    validate_sheet,
)
from app.history.tabular import Sheet, read_upload
from app.models import ImportFormat, Job, JobImportBatch, JobSource
from app.schemas.history import (
    AmbiguityOut,
    ColumnInfo,
    ConfirmOut,
    FieldInfo,
    HistoricalMoveIn,
    HistoricalMovePatch,
    HistoryDetailOut,
    HistoryRowOut,
    HistorySummaryOut,
    ImportBatchOut,
    ImportRequest,
    InspectOut,
    PreviewOut,
    RowIssueOut,
    RowVerdictOut,
)

logger = get_logger(__name__)

#: Detail rows returned by a preview. The counts always cover the whole file; this caps
#: the payload so a 10,000-row upload does not return 10,000 rows of explanation.
MAX_PREVIEW_ROWS = 200

#: Sample values shown per column so a person can recognise which column is which.
SAMPLE_VALUES = 3

#: Money fields arrive from hand entry in dollars and are stored in cents.
_DOLLAR_FIELDS = {
    "quoted_total_dollars": "quoted_total_cents",
    "actual_total_dollars": "actual_total_cents",
    "additional_charges_dollars": "additional_charges_cents",
}


def _identity(external_ref: str | None, row_hash: str | None) -> dict[str, str | None]:
    """Exactly one duplicate key per row, mirroring how duplicates are detected.

    A job number is the stronger identity, so when one is present the content hash is
    deliberately **not** stored. Storing both would make two genuinely different moves
    that happen to look identical collide on the content hash, even though their job
    numbers say they are distinct — which is precisely the case a company's own
    numbering exists to settle.
    """
    if external_ref:
        return {"external_ref": external_ref, "row_hash": None}
    return {"external_ref": None, "row_hash": row_hash}


def _format_value(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def _verdict_out(verdict: RowVerdict) -> RowVerdictOut:
    return RowVerdictOut(
        row_number=verdict.row_number,
        status=verdict.status.value,
        errors=[RowIssueOut(field=i.field, message=i.message) for i in verdict.errors],
        warnings=[RowIssueOut(field=i.field, message=i.message) for i in verdict.warnings],
        preview={k: _format_value(v) for k, v in verdict.values.items()},
    )


def _ambiguity_out(report: Any) -> AmbiguityOut:
    return AmbiguityOut(
        date_order=report.date_order.value if report.date_order else None,
        decimal_style=report.decimal_style.value if report.decimal_style else None,
        date_ambiguous=report.date_ambiguous,
        money_ambiguous=report.money_ambiguous,
        ambiguous_money_fields=list(report.ambiguous_money_fields),
        needs_input=report.needs_input,
    )


def _field_catalogue() -> list[FieldInfo]:
    return [
        FieldInfo(
            name=f.name,
            label=f.label,
            kind=f.kind.value,
            required=f.name in REQUIRED_FIELDS,
            outcome=f.name in OUTCOME_FIELDS,
            sensitive=f.sensitive,
        )
        for f in CANONICAL_FIELDS
    ]


def inspect_upload(filename: str, content: bytes) -> InspectOut:
    """Read an upload and propose a mapping. **Writes nothing.**"""
    sheet = read_upload(filename, content)
    proposed = suggestions(sheet.headers)
    by_header = {s.header: s for s in proposed}
    mapping = {s.field: s.header for s in proposed}
    report = inspect_ambiguity(sheet, mapping)

    columns = [
        ColumnInfo(
            header=header,
            sample_values=[
                str(v) for v in sheet.column(header)[:SAMPLE_VALUES] if v is not None
            ],
            suggested_field=by_header[header].field if header in by_header else None,
            confidence=by_header[header].confidence if header in by_header else None,
        )
        for header in sheet.headers
    ]
    return InspectOut(
        filename=filename,
        file_format=_format_of(filename).value,
        row_count=len(sheet.rows),
        columns=columns,
        fields=_field_catalogue(),
        suggested_mapping=mapping,
        ambiguity=_ambiguity_out(report),
    )


def _format_of(filename: str) -> ImportFormat:
    return ImportFormat.XLSX if (filename or "").lower().endswith(".xlsx") else ImportFormat.CSV


def _existing_keys(db: Session, company_id: uuid.UUID) -> tuple[frozenset[str], frozenset[str]]:
    """This company's already-imported identities: job numbers and content hashes."""
    refs = db.scalars(
        select(Job.external_ref).where(
            Job.company_id == company_id, Job.external_ref.is_not(None)
        )
    ).all()
    hashes = db.scalars(
        select(Job.row_hash).where(Job.company_id == company_id, Job.row_hash.is_not(None))
    ).all()
    return frozenset(r for r in refs if r), frozenset(h for h in hashes if h)


@dataclass(frozen=True)
class _Judged:
    sheet: Sheet
    verdicts: list[RowVerdict]
    options: ParseOptions
    report: Any


def _judge(
    db: Session, company_id: uuid.UUID, filename: str, content: bytes, request: ImportRequest
) -> _Judged:
    """Parse, resolve ambiguity, and judge every row. Shared by preview and confirm.

    Because confirm calls this too, an import is validated against the history as it is
    *at the moment of writing* — not as it was when the customer looked at the preview.
    """
    sheet = read_upload(filename, content)
    if not request.mapping:
        raise ValidationError("Map at least the required columns before importing")

    unknown_headers = sorted(set(request.mapping.values()) - set(sheet.headers))
    if unknown_headers:
        raise ValidationError(f"File has no column(s): {', '.join(unknown_headers)}")

    missing = [name for name in REQUIRED_FIELDS if name not in request.mapping]
    if missing:
        labels = ", ".join(FIELDS_BY_NAME[name].label for name in missing)
        raise ValidationError(f"These fields must be mapped: {labels}")
    if not any(name in request.mapping for name in OUTCOME_FIELDS):
        raise ValidationError("Map actual hours or a final total — otherwise there is no outcome")

    report = inspect_ambiguity(
        sheet,
        request.mapping,
        date_order=request.date_order,
        decimal_style=request.decimal_style,
    )
    if report.date_ambiguous:
        raise ValidationError(
            "The dates in this file could be day/month or month/day — tell us which "
            "before importing"
        )
    if report.money_ambiguous:
        raise ValidationError(
            "The amounts in this file could use either decimal style — tell us which "
            "before importing"
        )

    options = ParseOptions(
        date_order=report.date_order or DateOrder.ISO,
        decimal_style=report.decimal_style or DecimalStyle.DOT,
    )
    known_refs, known_hashes = _existing_keys(db, company_id)
    verdicts = validate_sheet(
        sheet,
        request.mapping,
        options,
        known_refs=known_refs,
        known_fingerprints=known_hashes,
    )
    return _Judged(sheet=sheet, verdicts=verdicts, options=options, report=report)


def _counts(verdicts: list[RowVerdict]) -> dict[str, int]:
    return {
        "total": len(verdicts),
        "importable": sum(1 for v in verdicts if v.importable),
        "warnings": sum(1 for v in verdicts if v.status is RowStatus.WARNING),
        "rejected": sum(1 for v in verdicts if v.status is RowStatus.ERROR),
        "duplicates": sum(1 for v in verdicts if v.status is RowStatus.DUPLICATE),
    }


def preview_import(
    db: Session,
    company_id: uuid.UUID,
    filename: str,
    content: bytes,
    request: ImportRequest,
) -> PreviewOut:
    """Show exactly what an import would do. **Writes nothing.**"""
    judged = _judge(db, company_id, filename, content, request)
    counts = _counts(judged.verdicts)

    # Problems first: with 200 slots, an owner should spend them on rows that need a
    # decision, not on the first 200 rows that happened to be fine.
    ordered = sorted(
        judged.verdicts,
        key=lambda v: ({RowStatus.ERROR: 0, RowStatus.WARNING: 1, RowStatus.DUPLICATE: 2}.get(
            v.status, 3
        ), v.row_number),
    )
    return PreviewOut(
        filename=filename,
        ambiguity=_ambiguity_out(judged.report),
        rows=[_verdict_out(v) for v in ordered[:MAX_PREVIEW_ROWS]],
        rows_truncated=len(judged.verdicts) > MAX_PREVIEW_ROWS,
        **counts,
    )


def confirm_import(
    db: Session,
    company_id: uuid.UUID,
    filename: str,
    content: bytes,
    request: ImportRequest,
    *,
    user_id: uuid.UUID | None = None,
) -> ConfirmOut:
    """Import the importable rows as one batch. The only write path for an upload."""
    judged = _judge(db, company_id, filename, content, request)
    counts = _counts(judged.verdicts)
    if counts["importable"] == 0:
        raise ConflictError(
            "Nothing to import — every row was rejected or already present. "
            "Check the preview before confirming."
        )

    batch = JobImportBatch(
        company_id=company_id,
        filename=filename[:255],
        file_format=_format_of(filename),
        column_mapping=dict(request.mapping),
        parse_options={
            "date_order": judged.options.date_order.value,
            "decimal_style": judged.options.decimal_style.value,
        },
        row_count_total=counts["total"],
        row_count_imported=counts["importable"],
        row_count_skipped=counts["duplicates"],
        row_count_rejected=counts["rejected"],
        created_by_user_id=user_id,
    )
    db.add(batch)
    db.flush()

    for verdict in judged.verdicts:
        if not verdict.importable:
            continue
        db.add(
            Job(
                company_id=company_id,
                booking_id=None,
                source=JobSource.IMPORT,
                import_batch_id=batch.id,
                **_identity(verdict.external_ref, verdict.fingerprint),
                **verdict.values,
            )
        )
    db.commit()

    logger.info(
        "History import for company %s: %d imported, %d duplicate, %d rejected (%s)",
        company_id,
        counts["importable"],
        counts["duplicates"],
        counts["rejected"],
        filename,
    )
    return ConfirmOut(
        batch=ImportBatchOut.model_validate(batch),
        rejected_rows=[
            _verdict_out(v)
            for v in judged.verdicts
            if v.status is RowStatus.ERROR
        ][:MAX_PREVIEW_ROWS],
    )


def list_batches(db: Session, company_id: uuid.UUID) -> list[ImportBatchOut]:
    rows = db.scalars(
        select(JobImportBatch)
        .where(JobImportBatch.company_id == company_id)
        .order_by(JobImportBatch.created_at.desc())
    )
    return [ImportBatchOut.model_validate(row) for row in rows]


def revert_batch(db: Session, company_id: uuid.UUID, batch_id: uuid.UUID) -> ImportBatchOut:
    """Delete the rows one batch created, so a bad mapping is undoable as a unit.

    Only rows still attributed to the batch are removed, and only imported ones — a
    platform job could never carry a batch id, but the filter makes that explicit rather
    than relying on it.
    """
    batch = db.scalar(
        select(JobImportBatch).where(
            JobImportBatch.id == batch_id, JobImportBatch.company_id == company_id
        )
    )
    if batch is None:
        raise NotFoundError("Import not found")
    if batch.reverted_at is not None:
        raise ConflictError("This import has already been reverted")

    removed = 0
    for job in db.scalars(
        select(Job).where(
            Job.company_id == company_id,
            Job.import_batch_id == batch.id,
            Job.source == JobSource.IMPORT,
        )
    ):
        db.delete(job)
        removed += 1

    batch.reverted_at = datetime.now(UTC)
    db.commit()
    logger.info("Reverted import %s for company %s: %d rows removed", batch.id, company_id, removed)
    return ImportBatchOut.model_validate(batch)


def _values_from_manual(payload: HistoricalMoveIn | HistoricalMovePatch) -> dict[str, object]:
    """Turn a hand-entered move into canonical values, dollars converted to cents."""
    values: dict[str, object] = {}
    for name, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        if name in _DOLLAR_FIELDS:
            values[_DOLLAR_FIELDS[name]] = round(float(value) * 100)
        else:
            values[name] = value
    return values


def create_move(
    db: Session, company_id: uuid.UUID, payload: HistoricalMoveIn
) -> HistoryDetailOut:
    """Add one historical move by hand, through the importer's own rules."""
    values = _values_from_manual(payload)
    external_ref = values.pop("external_ref", None)

    issues = check_business_rules(values)
    if issues:
        raise ValidationError("; ".join(issue.message for issue in issues))
    derive_building_keys(values)

    row_hash = fingerprint(values)
    if external_ref is not None:
        clash = db.scalar(
            select(Job.id).where(
                Job.company_id == company_id, Job.external_ref == str(external_ref)
            )
        )
    else:
        clash = db.scalar(
            select(Job.id).where(Job.company_id == company_id, Job.row_hash == row_hash)
        )
    if clash is not None:
        raise ConflictError("This move is already in your history")

    job = Job(
        company_id=company_id,
        booking_id=None,
        source=JobSource.IMPORT,
        **_identity(str(external_ref) if external_ref else None, row_hash),
        **values,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return HistoryDetailOut.model_validate(job)


def _owned(db: Session, company_id: uuid.UUID, move_id: uuid.UUID) -> Job:
    """Tenant predicate inside the lookup, so another company's move reads as absent."""
    job = db.scalar(select(Job).where(Job.id == move_id, Job.company_id == company_id))
    if job is None:
        raise NotFoundError("Historical move not found")
    return job


def get_move(db: Session, company_id: uuid.UUID, move_id: uuid.UUID) -> HistoryDetailOut:
    return HistoryDetailOut.model_validate(_owned(db, company_id, move_id))


def update_move(
    db: Session, company_id: uuid.UUID, move_id: uuid.UUID, payload: HistoricalMovePatch
) -> HistoryDetailOut:
    """Correct a stored move. The fingerprint is recomputed so identity tracks content."""
    job = _owned(db, company_id, move_id)
    for name, value in _values_from_manual(payload).items():
        setattr(job, name, value)

    current = {name: getattr(job, name) for name in FIELDS_BY_NAME if hasattr(job, name)}
    issues = check_business_rules(current)
    if issues:
        db.rollback()
        raise ValidationError("; ".join(issue.message for issue in issues))

    derive_building_keys(current)
    for side in ("origin", "destination"):
        key = f"{side}_building_key"
        if key in current:
            setattr(job, key, current[key])
    # Identity follows content only for rows that have no job number of their own.
    if not job.external_ref:
        job.row_hash = fingerprint(current)
    db.commit()
    db.refresh(job)
    return HistoryDetailOut.model_validate(job)


def delete_move(db: Session, company_id: uuid.UUID, move_id: uuid.UUID) -> None:
    db.delete(_owned(db, company_id, move_id))
    db.commit()


def list_moves(
    db: Session, company_id: uuid.UUID, *, source: str | None = None, limit: int = 200
) -> list[HistoryRowOut]:
    query = (
        select(Job)
        .where(Job.company_id == company_id)
        .order_by(Job.move_date.desc(), Job.id)
        .limit(min(max(limit, 1), 1000))
    )
    if source in {JobSource.PLATFORM.value, JobSource.IMPORT.value}:
        query = query.where(Job.source == JobSource(source))
    return [HistoryRowOut.model_validate(job) for job in db.scalars(query)]


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def summary(db: Session, company_id: uuid.UUID) -> HistorySummaryOut:
    """The numbers that reveal whether an import landed correctly.

    Medians rather than means throughout: one mis-keyed 400-hour move would drag a mean
    badly, and spotting bad data is this page's first job.
    """
    jobs = list(db.scalars(select(Job).where(Job.company_id == company_id)))
    hours = [j.actual_hours for j in jobs if j.actual_hours is not None]

    hour_errors = [
        (j.actual_hours - j.quoted_hours) / j.quoted_hours * 100
        for j in jobs
        if j.actual_hours is not None and j.quoted_hours
    ]
    price_errors = [
        (j.actual_total_cents - j.quoted_total_cents) / j.quoted_total_cents * 100
        for j in jobs
        if j.actual_total_cents is not None and j.quoted_total_cents
    ]
    dates = [j.move_date for j in jobs]

    return HistorySummaryOut(
        total_moves=len(jobs),
        imported_moves=sum(1 for j in jobs if j.source is JobSource.IMPORT),
        platform_moves=sum(1 for j in jobs if j.source is JobSource.PLATFORM),
        earliest_move_date=min(dates) if dates else None,
        latest_move_date=max(dates) if dates else None,
        median_actual_hours=_median(hours),
        median_hours_error_pct=_median(hour_errors),
        median_price_error_pct=_median(price_errors),
        moves_with_hours=len(hours),
        moves_with_estimate=sum(
            1 for j in jobs if j.quoted_hours is not None or j.quoted_total_cents is not None
        ),
    )


def count_moves(db: Session, company_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count()).select_from(Job).where(Job.company_id == company_id)
    ) or 0
