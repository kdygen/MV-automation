"""Integration tests for dashboard endpoints: lists, lead detail, settings."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.models import Company, User, UserRole
from tests.conftest import mint_token
from tests.test_quotes_api import submit


class TestLists:
    def test_leads_list_and_detail(self, client: TestClient, company, auth_headers) -> None:
        submit(client)
        leads = client.get("/api/v1/leads", headers=auth_headers).json()
        assert len(leads) == 1
        assert leads[0]["name"] == "Bob Customer"
        assert leads[0]["status"] == "quoted"

        detail = client.get(f"/api/v1/leads/{leads[0]['id']}", headers=auth_headers).json()
        assert detail["lead"]["email"] == "bob@example.com"
        assert len(detail["requests"]) == 1
        assert detail["requests"][0]["origin_city"] == "Springfield"
        assert len(detail["quotes"]) == 1
        assert detail["quotes"][0]["status"] == "sent"

    def test_leads_status_filter(self, client: TestClient, company, auth_headers) -> None:
        submit(client)
        assert client.get("/api/v1/leads?status=quoted", headers=auth_headers).json()
        assert client.get("/api/v1/leads?status=lost", headers=auth_headers).json() == []

    def test_quotes_list_with_review_queue_filter(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        submit(client)
        quotes = client.get("/api/v1/quotes", headers=auth_headers).json()
        assert len(quotes) == 1
        assert quotes[0]["lead_name"] == "Bob Customer"
        assert quotes[0]["home_size"] == "2br"
        # Instant-quote company has no drafts awaiting review.
        assert client.get("/api/v1/quotes?status=draft", headers=auth_headers).json() == []

    def test_bookings_list(self, client: TestClient, company, auth_headers) -> None:
        token = submit(client)["quote"]["public_token"]
        client.post(f"/api/v1/public/quotes/{token}/accept")
        bookings = client.get("/api/v1/bookings", headers=auth_headers).json()
        assert len(bookings) == 1
        assert bookings[0]["status"] == "confirmed"
        assert bookings[0]["lead_name"] == "Bob Customer"
        assert bookings[0]["amount_min_cents"] > 0

    def test_lists_are_tenant_scoped(self, client: TestClient, db, company, auth_headers) -> None:
        """Data created for another tenant never shows in this tenant's lists."""
        other = Company(name="Bravo", slug="bravo-van-lines", settings={})
        db.add(other)
        db.commit()
        submit(client, slug="bravo-van-lines")

        assert client.get("/api/v1/leads", headers=auth_headers).json() == []
        assert client.get("/api/v1/quotes", headers=auth_headers).json() == []
        assert client.get("/api/v1/bookings", headers=auth_headers).json() == []

    def test_lists_require_auth(self, client: TestClient, company) -> None:
        for path in ["/api/v1/leads", "/api/v1/quotes", "/api/v1/bookings"]:
            assert client.get(path).status_code == 401

    def test_cross_tenant_lead_detail_is_404(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        other = Company(name="Bravo", slug="bravo-van-lines", settings={})
        db.add(other)
        db.commit()
        lead_id = submit(client, slug="bravo-van-lines")["lead_id"]
        assert client.get(f"/api/v1/leads/{lead_id}", headers=auth_headers).status_code == 404


class TestCompanySettings:
    def test_get_defaults(self, client: TestClient, company, auth_headers) -> None:
        body = client.get("/api/v1/settings/company", headers=auth_headers).json()
        assert body["name"] == "Acme Movers"
        assert body["quote_review_mode"] is False
        assert body["quote_validity_days"] == 14

    def test_patch_updates_and_persists(self, client: TestClient, company, auth_headers) -> None:
        resp = client.patch(
            "/api/v1/settings/company",
            json={"quote_review_mode": True, "quote_validity_days": 7, "phone": "555-0111"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = client.get("/api/v1/settings/company", headers=auth_headers).json()
        assert body["quote_review_mode"] is True
        assert body["quote_validity_days"] == 7
        assert body["phone"] == "555-0111"
        assert body["name"] == "Acme Movers"  # untouched

    def test_review_mode_toggle_changes_quote_behavior(
        self, client: TestClient, company, auth_headers
    ) -> None:
        client.patch(
            "/api/v1/settings/company", json={"quote_review_mode": True}, headers=auth_headers
        )
        body = submit(client)
        assert body["quote"]["status"] == "pending_review"

    def test_staff_cannot_patch(self, client: TestClient, db, company) -> None:
        staff = User(
            id=uuid.uuid4(), company_id=company.id, email="s@acme.test", role=UserRole.STAFF
        )
        db.add(staff)
        db.commit()
        resp = client.patch(
            "/api/v1/settings/company",
            json={"name": "Hacked"},
            headers={"Authorization": f"Bearer {mint_token(staff.id)}"},
        )
        assert resp.status_code == 403


class TestPricingSettings:
    def test_get_seeds_default_config(self, client: TestClient, company, auth_headers) -> None:
        body = client.get("/api/v1/settings/pricing", headers=auth_headers).json()
        assert body["version"] == 1
        assert body["config"]["travel_fee_base"] == 50.0
        assert body["config"]["hourly_rate_by_crew"]["3"] == 190.0

    def test_put_creates_new_version_and_affects_quotes(
        self, client: TestClient, company, auth_headers
    ) -> None:
        config = client.get("/api/v1/settings/pricing", headers=auth_headers).json()["config"]
        baseline = submit(client)["quote"]["amount_min_cents"]

        config["hourly_rate_by_crew"] = {"2": 280.0, "3": 380.0, "4": 480.0, "5": 580.0}
        resp = client.put(
            "/api/v1/settings/pricing", json={"config": config}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 2

        pricier = submit(client)["quote"]["amount_min_cents"]
        assert pricier > baseline

    def test_put_rejects_invalid_config(self, client: TestClient, company, auth_headers) -> None:
        config = client.get("/api/v1/settings/pricing", headers=auth_headers).json()["config"]
        config["hourly_rate_by_crew"] = {"2": 140.0}  # missing rates for 3/4/5 crews
        resp = client.put(
            "/api/v1/settings/pricing", json={"config": config}, headers=auth_headers
        )
        assert resp.status_code == 422

    def test_staff_cannot_put(self, client: TestClient, db, company, auth_headers) -> None:
        config = client.get("/api/v1/settings/pricing", headers=auth_headers).json()["config"]
        staff = User(
            id=uuid.uuid4(), company_id=company.id, email="s2@acme.test", role=UserRole.STAFF
        )
        db.add(staff)
        db.commit()
        resp = client.put(
            "/api/v1/settings/pricing",
            json={"config": config},
            headers={"Authorization": f"Bearer {mint_token(staff.id)}"},
        )
        assert resp.status_code == 403
