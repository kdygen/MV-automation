"""Authenticated company-knowledge endpoints (the agent's answer book).

Reads are open to all staff; writes require owner/admin, matching ``/settings``. These
entries are statements the sales agent repeats to customers as company policy, so
changing one warrants the same bar as changing prices.

Tenancy: every handler passes ``user.company_id`` from the verified bearer token. No
route accepts a company identifier in its path, query, or body — the write schemas set
``extra="forbid"``, so a client that tries to send one gets a 422.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.db.session import get_db
from app.models import UserRole
from app.schemas.knowledge import (
    KnowledgeEntryIn,
    KnowledgeEntryOut,
    KnowledgeEntryPatch,
    StarterTopicOut,
)
from app.services import knowledge as knowledge_service

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_admin_only = require_roles(UserRole.OWNER, UserRole.ADMIN)


# Declared before the /{entry_id} routes so a literal path segment is never considered
# as an id, whatever order FastAPI resolves in.
@router.get("/starters", response_model=list[StarterTopicOut])
def list_starter_topics(
    user: CurrentUser = Depends(get_current_user),
) -> list[StarterTopicOut]:
    """Suggested onboarding topics: questions and search keywords, never answers."""
    return knowledge_service.list_starter_topics()


@router.get("", response_model=list[KnowledgeEntryOut])
def list_knowledge(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KnowledgeEntryOut]:
    """Every entry for this company, including deactivated ones."""
    return knowledge_service.list_entries(db, user.company_id)


@router.post("", response_model=KnowledgeEntryOut, status_code=status.HTTP_201_CREATED)
def create_knowledge(
    payload: KnowledgeEntryIn,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> KnowledgeEntryOut:
    return knowledge_service.create_entry(db, user.company_id, payload)


@router.patch("/{entry_id}", response_model=KnowledgeEntryOut)
def update_knowledge(
    entry_id: uuid.UUID,
    payload: KnowledgeEntryPatch,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> KnowledgeEntryOut:
    """Partial update, including the ``is_active`` toggle."""
    return knowledge_service.update_entry(db, user.company_id, entry_id, payload)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(
    entry_id: uuid.UUID,
    user: CurrentUser = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> Response:
    knowledge_service.delete_entry(db, user.company_id, entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
