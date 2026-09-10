"""Shared test fixtures.

The suite runs with zero external services:

- Database: in-memory SQLite with ``StaticPool`` (one shared connection across the
  session), schema created via ``Base.metadata.create_all``. Alembic migrations are for
  real databases; model metadata is the schema source of truth for tests.
- Settings: a ``Settings`` instance with a known JWT secret so tests can mint real
  HS256 tokens and exercise the full verification path.
- App: built by ``create_app`` with ``get_db``/``get_settings`` dependency overrides.

Each test function gets a fresh database (tables dropped/recreated) for isolation.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import email_provider_dep
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import Company, CompanyKnowledge, User, UserRole
from app.providers.email import FakeEmailProvider
from scripts.seed_dev import SEED_KNOWLEDGE

TEST_JWT_SECRET = "test-secret-not-for-production-0123456789abcdef"


@pytest.fixture(scope="session")
def engine():
    """Session-wide in-memory SQLite engine shared across threads."""
    return create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@pytest.fixture()
def db(engine) -> Iterator[Session]:
    """A fresh schema and session per test."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def test_settings() -> Settings:
    """Settings with a deterministic JWT secret and test environment."""
    return Settings(
        environment="test",
        debug=True,
        supabase_jwt_secret=TEST_JWT_SECRET,
        database_url="sqlite+pysqlite://",  # unused: get_db is overridden
        _env_file=None,  # do not read a developer's local .env in tests
    )


@pytest.fixture()
def email_outbox() -> FakeEmailProvider:
    """Per-test email provider; assert on ``email_outbox.outbox``."""
    return FakeEmailProvider()


@pytest.fixture()
def app(db: Session, test_settings: Settings, email_outbox: FakeEmailProvider) -> FastAPI:
    """Application instance with DB, settings, and provider dependencies overridden."""
    application = create_app(test_settings)

    def _override_get_db() -> Iterator[Session]:
        yield db

    application.dependency_overrides[get_db] = _override_get_db
    application.dependency_overrides[get_settings] = lambda: test_settings
    application.dependency_overrides[email_provider_dep] = lambda: email_outbox
    return application


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ---------- Domain fixtures ----------


@pytest.fixture()
def company(db: Session) -> Company:
    c = Company(name="Acme Movers", slug="acme-movers", email="ops@acme.test", settings={})
    db.add(c)
    db.commit()
    return c


@pytest.fixture()
def knowledge(db: Session, company: Company) -> list[CompanyKnowledge]:
    """Load the demo knowledge base for ``company``.

    Imported from the dev seed rather than re-written here, so the entries developers
    actually run against are the ones the search tests exercise.
    """
    entries = [CompanyKnowledge(company_id=company.id, **entry) for entry in SEED_KNOWLEDGE]
    db.add_all(entries)
    db.commit()
    return entries


@pytest.fixture()
def owner(db: Session, company: Company) -> User:
    u = User(
        id=uuid.uuid4(),
        company_id=company.id,
        email="owner@acme.test",
        full_name="Alice Owner",
        role=UserRole.OWNER,
    )
    db.add(u)
    db.commit()
    return u


def mint_token(
    user_id: uuid.UUID,
    *,
    secret: str = TEST_JWT_SECRET,
    audience: str = "authenticated",
    expires_in: timedelta = timedelta(hours=1),
) -> str:
    """Mint a Supabase-shaped HS256 access token for tests."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "role": "authenticated",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture()
def auth_headers(owner: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(owner.id)}"}
