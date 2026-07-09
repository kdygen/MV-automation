"""Dashboard read/update services: tenant-scoped listings and settings.

Every query here filters by ``company_id`` — the caller passes the authenticated
user's tenant, never a client-supplied one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models import Booking, Company, Lead, MovingRequest, Quote
from app.schemas.dashboard import (
    BookingOut,
    CompanySettingsIn,
    CompanySettingsOut,
    LeadDetailOut,
    LeadOut,
    QuoteListOut,
    RequestAdminOut,
)
from app.services.quotes import company_review_mode, company_validity_days


def list_leads(db: Session, company_id: uuid.UUID, *, status: str | None = None) -> list[LeadOut]:
    query = select(Lead).where(Lead.company_id == company_id).order_by(Lead.created_at.desc())
    if status:
        query = query.where(Lead.status == status)
    return [LeadOut.model_validate(lead) for lead in db.scalars(query)]


def get_lead_detail(db: Session, company_id: uuid.UUID, lead_id: uuid.UUID) -> LeadDetailOut:
    lead = db.scalar(select(Lead).where(Lead.id == lead_id, Lead.company_id == company_id))
    if lead is None:
        raise NotFoundError("Lead not found")
    requests = list(
        db.scalars(
            select(MovingRequest)
            .where(MovingRequest.lead_id == lead.id)
            .order_by(MovingRequest.created_at.desc())
        )
    )
    quotes = _quote_rows(
        db,
        select(Quote, Lead, MovingRequest)
        .join(MovingRequest, Quote.moving_request_id == MovingRequest.id)
        .join(Lead, MovingRequest.lead_id == Lead.id)
        .where(Lead.id == lead.id)
        .order_by(Quote.created_at.desc()),
    )
    return LeadDetailOut(
        lead=LeadOut.model_validate(lead),
        requests=[RequestAdminOut.model_validate(r) for r in requests],
        quotes=quotes,
    )


def list_quotes(
    db: Session, company_id: uuid.UUID, *, status: str | None = None
) -> list[QuoteListOut]:
    query = (
        select(Quote, Lead, MovingRequest)
        .join(MovingRequest, Quote.moving_request_id == MovingRequest.id)
        .join(Lead, MovingRequest.lead_id == Lead.id)
        .where(Quote.company_id == company_id)
        .order_by(Quote.created_at.desc())
    )
    if status:
        query = query.where(Quote.status == status)
    return _quote_rows(db, query)


def _quote_rows(db: Session, query) -> list[QuoteListOut]:  # type: ignore[no-untyped-def]
    rows = db.execute(query).all()
    return [
        QuoteListOut(
            id=quote.id,
            status=quote.status.value,
            currency=quote.currency,
            amount_min_cents=quote.amount_min_cents,
            amount_max_cents=quote.amount_max_cents,
            is_adjusted=quote.is_adjusted,
            created_at=quote.created_at,
            valid_until=quote.valid_until,
            lead_name=lead.name,
            lead_email=lead.email,
            move_date=request.move_date,
            home_size=request.home_size.value,
        )
        for quote, lead, request in rows
    ]


def list_bookings(
    db: Session, company_id: uuid.UUID, *, status: str | None = None
) -> list[BookingOut]:
    query = (
        select(Booking, Quote, Lead)
        .join(Quote, Booking.quote_id == Quote.id)
        .join(MovingRequest, Quote.moving_request_id == MovingRequest.id)
        .join(Lead, MovingRequest.lead_id == Lead.id)
        .where(Booking.company_id == company_id)
        .order_by(Booking.scheduled_date.asc())
    )
    if status:
        query = query.where(Booking.status == status)
    return [
        BookingOut(
            id=booking.id,
            scheduled_date=booking.scheduled_date,
            time_window=booking.time_window,
            crew_size=booking.crew_size,
            status=booking.status.value,
            notes=booking.notes,
            lead_name=lead.name,
            lead_email=lead.email,
            amount_min_cents=quote.amount_min_cents,
            amount_max_cents=quote.amount_max_cents,
            quote_id=quote.id,
        )
        for booking, quote, lead in db.execute(query).all()
    ]


def get_company_settings(db: Session, company_id: uuid.UUID) -> CompanySettingsOut:
    company = db.get(Company, company_id)
    assert company is not None  # authenticated user's own tenant
    return CompanySettingsOut(
        name=company.name,
        slug=company.slug,
        email=company.email,
        phone=company.phone,
        quote_review_mode=company_review_mode(company),
        quote_validity_days=company_validity_days(company),
    )


def update_company_settings(
    db: Session, company_id: uuid.UUID, payload: CompanySettingsIn
) -> CompanySettingsOut:
    company = db.get(Company, company_id)
    assert company is not None

    if payload.name is not None:
        company.name = payload.name
    if payload.email is not None:
        company.email = payload.email
    if payload.phone is not None:
        company.phone = payload.phone

    settings_patch: dict[str, object] = {}
    if payload.quote_review_mode is not None:
        settings_patch["quote_review_mode"] = payload.quote_review_mode
    if payload.quote_validity_days is not None:
        settings_patch["quote_validity_days"] = payload.quote_validity_days
    if settings_patch:
        # Reassign (not mutate) so SQLAlchemy detects the JSON change.
        company.settings = {**company.settings, **settings_patch}

    db.commit()
    return get_company_settings(db, company_id)
