"""Tests for the staff-provisioning logic (scripts/provision_staff.py)."""

from __future__ import annotations

import uuid

import pytest

from app.models import User, UserRole
from scripts.provision_staff import provision_staff


def test_provisions_new_staff_user(db, company) -> None:
    auth_id = uuid.uuid4()
    user, created = provision_staff(
        db,
        auth_user_id=auth_id,
        company_slug="acme-movers",
        email="boss@acme.test",
        role=UserRole.OWNER,
        full_name="Alice Boss",
    )
    assert created is True
    stored = db.get(User, auth_id)
    assert stored is not None
    assert stored.company_id == company.id
    assert stored.role is UserRole.OWNER
    assert stored.full_name == "Alice Boss"


def test_reprovisioning_updates_instead_of_duplicating(db, company) -> None:
    auth_id = uuid.uuid4()
    provision_staff(
        db,
        auth_user_id=auth_id,
        company_slug="acme-movers",
        email="boss@acme.test",
        role=UserRole.STAFF,
    )
    user, created = provision_staff(
        db,
        auth_user_id=auth_id,
        company_slug="acme-movers",
        email="boss@acme.test",
        role=UserRole.ADMIN,
    )
    assert created is False
    assert db.get(User, auth_id).role is UserRole.ADMIN


def test_unknown_company_slug_raises(db) -> None:
    with pytest.raises(LookupError, match="nope-movers"):
        provision_staff(
            db,
            auth_user_id=uuid.uuid4(),
            company_slug="nope-movers",
            email="x@y.test",
        )
