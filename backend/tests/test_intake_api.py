"""Integration tests for the public intake endpoint."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Lead, LeadStatus, MovingRequest, RequestStatus


def make_payload(**overrides: Any) -> dict[str, Any]:
    """A valid submission; override individual top-level keys per test."""
    payload: dict[str, Any] = {
        "contact": {"name": "Bob Customer", "email": "bob@example.com", "phone": "555-0100"},
        "origin": {
            "line1": "12 Elm St",
            "city": "Springfield",
            "state": "il",
            "zip": "62701",
            "floor": 3,
            "has_elevator": False,
            "stairs_flights": 2,
        },
        "destination": {
            "line1": "99 Oak Ave",
            "city": "Chatham",
            "state": "IL",
            "zip": "62629",
        },
        "move_date": (date.today() + timedelta(days=30)).isoformat(),
        "is_date_flexible": True,
        "home_size": "2br",
        "packing_service": "partial",
        "special_items": ["piano"],
        "notes": "Gate code is 4321",
    }
    payload.update(overrides)
    return payload


def test_submit_creates_lead_and_request(client: TestClient, db, company) -> None:
    resp = client.post("/api/v1/public/acme-movers/requests", json=make_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Instant quote (Milestone 4): priceable submissions come back quoted.
    assert body["quote"] is not None
    assert body["request"]["home_size"] == "2br"
    assert body["request"]["status"] == "quoted"
    assert body["request"]["distance_miles"] is not None

    lead = db.scalar(select(Lead))
    request = db.scalar(select(MovingRequest))
    assert lead is not None and request is not None
    assert str(lead.id) == body["lead_id"]
    assert lead.company_id == company.id
    assert lead.status is LeadStatus.QUOTED
    assert request.lead_id == lead.id
    assert request.company_id == company.id
    assert request.status is RequestStatus.QUOTED
    # State normalized to uppercase; raw payload preserved for audit.
    assert request.origin_state == "IL"
    assert request.raw_payload["contact"]["email"] == "bob@example.com"


def test_unknown_company_slug_is_404(client: TestClient) -> None:
    resp = client.post("/api/v1/public/nobody-here/requests", json=make_payload())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_past_move_date_rejected(client: TestClient, company) -> None:
    resp = client.post(
        "/api/v1/public/acme-movers/requests",
        json=make_payload(move_date=(date.today() - timedelta(days=1)).isoformat()),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_bad_zip_rejected(client: TestClient, company) -> None:
    payload = make_payload()
    payload["origin"]["zip"] = "abcde"
    resp = client.post("/api/v1/public/acme-movers/requests", json=payload)
    assert resp.status_code == 422


def test_bad_email_rejected(client: TestClient, company) -> None:
    payload = make_payload()
    payload["contact"]["email"] = "not-an-email"
    resp = client.post("/api/v1/public/acme-movers/requests", json=payload)
    assert resp.status_code == 422


def test_bad_home_size_rejected(client: TestClient, company) -> None:
    resp = client.post(
        "/api/v1/public/acme-movers/requests", json=make_payload(home_size="mansion")
    )
    assert resp.status_code == 422


def test_requests_are_tenant_scoped(client: TestClient, db, company) -> None:
    """Submissions land on the company in the URL, not any other tenant."""
    from app.models import Company

    other = Company(name="Bravo Van Lines", slug="bravo-van-lines", settings={})
    db.add(other)
    db.commit()

    client.post("/api/v1/public/bravo-van-lines/requests", json=make_payload())

    leads = db.scalars(select(Lead)).all()
    assert len(leads) == 1
    assert leads[0].company_id == other.id


def test_distance_failure_still_stores_request(client: TestClient, app, company, db) -> None:
    """A maps outage must never lose a lead: request persists with distance=None."""
    from app.api.v1.public import distance_provider_dep

    class ExplodingProvider:
        def distance_miles(self, origin, destination):  # type: ignore[no-untyped-def]
            raise RuntimeError("maps are down")

    app.dependency_overrides[distance_provider_dep] = lambda: ExplodingProvider()
    resp = client.post("/api/v1/public/acme-movers/requests", json=make_payload())
    assert resp.status_code == 201
    assert resp.json()["request"]["distance_miles"] is None
    assert db.scalar(select(MovingRequest)) is not None
