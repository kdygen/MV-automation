"""Integration tests for the authenticated API surface (/api/v1/me)."""

import uuid

from fastapi.testclient import TestClient

from app.models import User, UserRole
from tests.conftest import mint_token


def test_me_returns_identity_and_tenant(client: TestClient, auth_headers, owner) -> None:
    resp = client.get("/api/v1/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(owner.id)
    assert body["company_id"] == str(owner.company_id)
    assert body["email"] == "owner@acme.test"
    assert body["role"] == "owner"


def test_me_without_token_is_401(client: TestClient) -> None:
    resp = client.get("/api/v1/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_me_with_invalid_token_is_401(client: TestClient) -> None:
    resp = client.get("/api/v1/me", headers={"Authorization": "Bearer nonsense"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_me_with_unprovisioned_user_is_403(client: TestClient, company) -> None:
    # A valid Supabase identity that has no row in our users table.
    resp = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {mint_token(uuid.uuid4())}"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_users_are_scoped_to_their_company(client: TestClient, db, company) -> None:
    """Two users in two companies resolve to distinct tenants — the tenancy backbone."""
    from app.models import Company

    other = Company(name="Bravo Van Lines", slug="bravo-van-lines", settings={})
    db.add(other)
    db.commit()

    u1 = User(id=uuid.uuid4(), company_id=company.id, email="a@acme.test", role=UserRole.STAFF)
    u2 = User(id=uuid.uuid4(), company_id=other.id, email="b@bravo.test", role=UserRole.STAFF)
    db.add_all([u1, u2])
    db.commit()

    r1 = client.get("/api/v1/me", headers={"Authorization": f"Bearer {mint_token(u1.id)}"})
    r2 = client.get("/api/v1/me", headers={"Authorization": f"Bearer {mint_token(u2.id)}"})
    assert r1.json()["company_id"] == str(company.id)
    assert r2.json()["company_id"] == str(other.id)
    assert r1.json()["company_id"] != r2.json()["company_id"]
