"""Integration tests for the quote lifecycle: instant quote on intake, customer
view/accept/decline via token, expiry, review mode, and owner approval."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import (
    Booking,
    BookingStatus,
    Company,
    Lead,
    LeadStatus,
    Quote,
    QuoteStatus,
    User,
    UserRole,
)
from tests.conftest import mint_token
from tests.test_intake_api import make_payload


def submit(client: TestClient, slug: str = "acme-movers") -> dict:
    resp = client.post(f"/api/v1/public/{slug}/requests", json=make_payload())
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestInstantQuoteOnIntake:
    def test_intake_returns_sent_quote_with_amounts_and_token(
        self, client, company, email_outbox
    ) -> None:
        body = submit(client)
        quote = body["quote"]
        assert quote is not None
        assert quote["status"] == "sent"
        assert quote["public_token"]
        assert quote["amount_min_cents"] > 0
        assert quote["amount_max_cents"] >= quote["amount_min_cents"]
        assert any(li["code"] == "labor" for li in quote["line_items"])
        # Customer got the quote email with the link.
        assert len(email_outbox.outbox) == 1
        sent = email_outbox.outbox[0]
        assert sent.to == "bob@example.com"
        assert quote["public_token"] in sent.text_body

    def test_request_and_lead_move_to_quoted(self, client, db, company) -> None:
        submit(client)
        lead = db.scalar(select(Lead))
        quote = db.scalar(select(Quote))
        assert lead.status is LeadStatus.QUOTED
        assert quote.status is QuoteStatus.SENT
        assert quote.engine_version.startswith("rules-")

    def test_review_mode_returns_pending_review_without_price(
        self, client, db, email_outbox
    ) -> None:
        review_co = Company(
            name="Careful Movers",
            slug="careful-movers",
            email="boss@careful.test",
            settings={"quote_review_mode": True},
        )
        db.add(review_co)
        db.commit()

        body = submit(client, slug="careful-movers")
        assert body["quote"]["status"] == "pending_review"
        assert body["quote"]["public_token"] is None
        assert body["quote"]["amount_min_cents"] is None

        quote = db.scalar(select(Quote))
        assert quote.status is QuoteStatus.DRAFT
        # Owner (not customer) got the review email.
        assert [m.to for m in email_outbox.outbox] == ["boss@careful.test"]

    def test_unpriceable_request_stays_pending_without_quote(
        self, client, app, db, company
    ) -> None:
        from app.api.deps import distance_provider_dep

        class ExplodingProvider:
            def distance_miles(self, origin, destination):  # type: ignore[no-untyped-def]
                raise RuntimeError("maps down")

        app.dependency_overrides[distance_provider_dep] = lambda: ExplodingProvider()
        body = submit(client)
        assert body["quote"] is None
        assert body["request"]["status"] == "pending"
        assert db.scalar(select(Quote)) is None


class TestCustomerQuoteFlow:
    def test_view_quote_by_token(self, client, company) -> None:
        token = submit(client)["quote"]["public_token"]
        resp = client.get(f"/api/v1/public/quotes/{token}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["company_name"] == "Acme Movers"
        assert body["status"] == "sent"
        assert body["origin_city"] == "Springfield"
        assert body["destination_city"] == "Chatham"
        assert body["amount_min_cents"] > 0

    def test_unknown_token_is_404(self, client, company) -> None:
        assert client.get("/api/v1/public/quotes/nope").status_code == 404

    def test_accept_creates_booking_and_advances_statuses(
        self, client, db, company, email_outbox
    ) -> None:
        token = submit(client)["quote"]["public_token"]
        email_outbox.outbox.clear()

        resp = client.post(f"/api/v1/public/quotes/{token}/accept")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "confirmed"
        assert body["company_name"] == "Acme Movers"

        booking = db.scalar(select(Booking))
        quote = db.scalar(select(Quote))
        lead = db.scalar(select(Lead))
        assert booking is not None
        assert booking.status is BookingStatus.CONFIRMED
        assert booking.quote_id == quote.id
        assert str(booking.id) == body["booking_id"]
        assert quote.status is QuoteStatus.ACCEPTED
        assert quote.accepted_at is not None
        assert lead.status is LeadStatus.BOOKED
        # Customer confirmation + company heads-up.
        assert sorted(m.to for m in email_outbox.outbox) == [
            "bob@example.com",
            "ops@acme.test",
        ]

    def test_accept_twice_conflicts(self, client, company) -> None:
        token = submit(client)["quote"]["public_token"]
        assert client.post(f"/api/v1/public/quotes/{token}/accept").status_code == 200
        resp = client.post(f"/api/v1/public/quotes/{token}/accept")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "conflict"

    def test_decline_marks_lead_lost(self, client, db, company) -> None:
        token = submit(client)["quote"]["public_token"]
        resp = client.post(f"/api/v1/public/quotes/{token}/decline")
        assert resp.status_code == 200
        assert resp.json()["status"] == "declined"
        assert db.scalar(select(Lead)).status is LeadStatus.LOST
        assert db.scalar(select(Booking)) is None

    def test_expired_quote_cannot_be_accepted(self, client, db, company) -> None:
        token = submit(client)["quote"]["public_token"]
        quote = db.scalar(select(Quote))
        quote.valid_until = datetime.now(UTC) - timedelta(days=1)
        db.commit()

        view = client.get(f"/api/v1/public/quotes/{token}")
        assert view.json()["status"] == "expired"
        resp = client.post(f"/api/v1/public/quotes/{token}/accept")
        assert resp.status_code == 409
        assert "expired" in resp.json()["error"]["message"].lower()


class TestOwnerApproval:
    def _draft_quote(self, client, db) -> tuple[Company, Quote]:
        review_co = Company(
            name="Careful Movers",
            slug="careful-movers",
            email="boss@careful.test",
            settings={"quote_review_mode": True},
        )
        db.add(review_co)
        db.commit()
        submit(client, slug="careful-movers")
        quote = db.scalar(select(Quote))
        assert quote.status is QuoteStatus.DRAFT
        return review_co, quote

    def _owner_headers(self, db, company: Company) -> dict[str, str]:
        owner = User(
            id=uuid.uuid4(),
            company_id=company.id,
            email="boss@careful.test",
            role=UserRole.OWNER,
        )
        db.add(owner)
        db.commit()
        return {"Authorization": f"Bearer {mint_token(owner.id)}"}

    def test_owner_approves_draft_and_customer_is_emailed(
        self, client, db, email_outbox
    ) -> None:
        review_co, quote = self._draft_quote(client, db)
        headers = self._owner_headers(db, review_co)
        email_outbox.outbox.clear()

        resp = client.post(
            f"/api/v1/quotes/{quote.id}/approve", json={}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "sent"
        assert [m.to for m in email_outbox.outbox] == ["bob@example.com"]

    def test_owner_can_adjust_range_on_approval(self, client, db) -> None:
        review_co, quote = self._draft_quote(client, db)
        headers = self._owner_headers(db, review_co)

        resp = client.post(
            f"/api/v1/quotes/{quote.id}/approve",
            json={"amount_min_dollars": 1500.0, "amount_max_dollars": 1800.0},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["amount_min_cents"] == 150_000
        assert body["amount_max_cents"] == 180_000
        assert body["is_adjusted"] is True

    def test_staff_cannot_approve(self, client, db) -> None:
        review_co, quote = self._draft_quote(client, db)
        staff = User(
            id=uuid.uuid4(),
            company_id=review_co.id,
            email="staff@careful.test",
            role=UserRole.STAFF,
        )
        db.add(staff)
        db.commit()
        resp = client.post(
            f"/api/v1/quotes/{quote.id}/approve",
            json={},
            headers={"Authorization": f"Bearer {mint_token(staff.id)}"},
        )
        assert resp.status_code == 403

    def test_approving_sent_quote_conflicts(self, client, db, company, auth_headers) -> None:
        submit(client)  # instant-quote company: quote is already sent
        quote = db.scalar(select(Quote))
        resp = client.post(f"/api/v1/quotes/{quote.id}/approve", json={}, headers=auth_headers)
        assert resp.status_code == 409

    def test_quotes_are_tenant_scoped(self, client, db, company, auth_headers) -> None:
        """acme's owner cannot see or approve another company's quote."""
        review_co, quote = self._draft_quote(client, db)  # quote belongs to careful-movers
        assert (
            client.get(f"/api/v1/quotes/{quote.id}", headers=auth_headers).status_code == 404
        )
        assert (
            client.post(
                f"/api/v1/quotes/{quote.id}/approve", json={}, headers=auth_headers
            ).status_code
            == 404
        )
