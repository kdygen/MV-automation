"""Authenticated quote endpoints (dashboard actions).

Approval is restricted to owners/admins — staff can view but not release prices to
customers. All lookups are tenant-scoped through the authenticated user's company.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import (
    AnyEmailProvider,
    CurrentUser,
    email_provider_dep,
    get_current_user,
    quote_public_url,
    require_roles,
)
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Company, Lead, MovingRequest, UserRole
from app.schemas.dashboard import QuoteListOut
from app.schemas.quotes import ApproveQuoteIn, QuoteAdminOut
from app.services import dashboard, notifications
from app.services import quotes as quote_service

router = APIRouter(prefix="/quotes", tags=["quotes"])


def _to_cents(dollars: float | None) -> int | None:
    return None if dollars is None else round(dollars * 100)


@router.get("", response_model=list[QuoteListOut])
def list_quotes(
    status: str | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[QuoteListOut]:
    """All quotes for the company, newest first; filter with ?status=draft for the
    review queue."""
    return dashboard.list_quotes(db, user.company_id, status=status)


@router.get("/{quote_id}", response_model=QuoteAdminOut)
def get_quote(
    quote_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuoteAdminOut:
    """Full quote detail for the dashboard (tenant-scoped)."""
    quote = quote_service.get_quote_for_company(db, company_id=user.company_id, quote_id=quote_id)
    return QuoteAdminOut.model_validate(quote, from_attributes=True)


@router.post("/{quote_id}/approve", response_model=QuoteAdminOut)
def approve_quote(
    quote_id: uuid.UUID,
    payload: ApproveQuoteIn,
    user: CurrentUser = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    email_provider: AnyEmailProvider = Depends(email_provider_dep),
) -> QuoteAdminOut:
    """Approve a draft quote (optionally adjusting the range); emails the customer."""
    quote = quote_service.get_quote_for_company(db, company_id=user.company_id, quote_id=quote_id)
    quote = quote_service.approve_quote(
        db,
        quote,
        amount_min_cents=_to_cents(payload.amount_min_dollars),
        amount_max_cents=_to_cents(payload.amount_max_dollars),
    )

    request = db.get(MovingRequest, quote.moving_request_id)
    assert request is not None
    lead = db.get(Lead, request.lead_id)
    company = db.get(Company, quote.company_id)
    assert lead is not None and company is not None

    notifications.send_quote_to_customer(
        email_provider,
        quote=quote,
        lead=lead,
        company=company,
        quote_url=quote_public_url(settings, quote.public_token),
    )
    return QuoteAdminOut.model_validate(quote, from_attributes=True)
