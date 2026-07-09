"""Model-layer tests: identity, defaults, constraints."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Company, User, UserRole


def test_company_gets_uuid_and_timestamps(db) -> None:
    c = Company(name="Test Co", slug="test-co", settings={})
    db.add(c)
    db.commit()
    assert isinstance(c.id, uuid.UUID)
    assert c.created_at is not None
    assert c.updated_at is not None
    assert c.settings == {}


def test_company_slug_must_be_unique(db) -> None:
    db.add(Company(name="One", slug="same-slug", settings={}))
    db.commit()
    db.add(Company(name="Two", slug="same-slug", settings={}))
    with pytest.raises(IntegrityError):
        db.commit()


def test_user_role_roundtrips_as_enum(db, company) -> None:
    u = User(id=uuid.uuid4(), company_id=company.id, email="x@y.test", role=UserRole.ADMIN)
    db.add(u)
    db.commit()
    db.expire_all()
    loaded = db.get(User, u.id)
    assert loaded is not None
    assert loaded.role is UserRole.ADMIN


def test_company_settings_json_roundtrip(db) -> None:
    c = Company(
        name="JSON Co",
        slug="json-co",
        settings={"quote_review_mode": True, "quote_validity_days": 14},
    )
    db.add(c)
    db.commit()
    db.expire_all()
    loaded = db.get(Company, c.id)
    assert loaded is not None
    assert loaded.settings["quote_review_mode"] is True
    assert loaded.settings["quote_validity_days"] == 14
