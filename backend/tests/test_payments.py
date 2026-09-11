"""Tests for the pay-first payment lifecycle (Step 4E).

The security claims under test, stated as attacks:

* the browser cannot influence the amount;
* landing on the success URL does not confirm a booking;
* an unsigned or wrongly-signed webhook changes nothing;
* a duplicate webhook does not create a second booking;
* a failed or abandoned checkout leaves the quote exactly as it was.

Everything runs against :class:`FakePaymentProvider` — no key, no network, no money —
which still performs a real HMAC signature check, so the verification path is genuinely
exercised rather than stubbed.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.api.deps import payment_provider_dep
from app.models import Booking, Payment, PaymentStatus, ProcessedWebhookEvent, Quote, QuoteStatus
from app.providers.payment import FakePaymentProvider
from app.services import payments as payment_service
from tests.test_date_change import quote_of
from tests.test_quotes_api import submit

WEBHOOK_URL = "/api/v1/webhooks/stripe"


@pytest.fixture()
def provider(app) -> FakePaymentProvider:
    """Install an offline payment provider for the app under test."""
    fake = FakePaymentProvider(secret="test-webhook-secret")
    app.dependency_overrides[payment_provider_dep] = lambda: fake
    return fake


@pytest.fixture()
def deposit_company(db, company):
    """A company that takes a $200 fixed deposit."""
    company.settings = {"deposit_type": "fixed", "deposit_amount_cents": 20_000}
    db.commit()
    return company


@pytest.fixture()
def live(client, db, deposit_company):
    body = submit(client)
    token = body["quote"]["public_token"]
    return {"token": token, "quote": quote_of(db, token), "company": deposit_company}


def checkout(client, token: str):
    return client.post(f"/api/v1/public/quotes/{token}/checkout")


def send_event(client, provider: FakePaymentProvider, body: dict, *, signature=None):
    payload = json.dumps(body).encode()
    return client.post(
        WEBHOOK_URL,
        content=payload,
        headers={"Stripe-Signature": signature or provider.sign(payload)},
    )


def success_event(session_id: str, *, event_id: str = "evt_1") -> dict:
    return {
        "id": event_id,
        "kind": "succeeded",
        "session_id": session_id,
        "payment_intent_id": "pi_123",
    }


class TestDepositConfiguration:
    def test_no_deposit_by_default(self, db, company) -> None:
        """Today's behaviour is preserved for every company that configures nothing."""
        lead, request, quote = submit_chain(db, company)
        assert payment_service.deposit_cents(company, quote) == 0
        assert payment_service.requires_payment(company, quote) is False

    def test_fixed_deposit(self, db, company) -> None:
        company.settings = {"deposit_type": "fixed", "deposit_amount_cents": 15_000}
        db.commit()
        lead, request, quote = submit_chain(db, company)
        assert payment_service.deposit_cents(company, quote) == 15_000

    def test_percent_deposit_uses_the_bottom_of_the_range(self, db, company) -> None:
        company.settings = {"deposit_type": "percent", "deposit_percent": 10}
        db.commit()
        lead, request, quote = submit_chain(db, company)
        assert payment_service.deposit_cents(company, quote) == round(quote.amount_min_cents * 0.10)

    def test_deposit_never_exceeds_the_quoted_minimum(self, db, company) -> None:
        company.settings = {"deposit_type": "fixed", "deposit_amount_cents": 99_999_999}
        db.commit()
        lead, request, quote = submit_chain(db, company)
        assert payment_service.deposit_cents(company, quote) == quote.amount_min_cents

    def test_trivial_deposits_are_treated_as_none(self, db, company) -> None:
        company.settings = {"deposit_type": "fixed", "deposit_amount_cents": 5}
        db.commit()
        lead, request, quote = submit_chain(db, company)
        assert payment_service.deposit_cents(company, quote) == 0

    def test_deposit_is_published_on_the_quote_page(self, client, live) -> None:
        page = client.get(f"/api/v1/public/quotes/{live['token']}").json()
        assert page["deposit_cents"] == 20_000


def submit_chain(db, company):
    """A sent quote for ``company`` without going through HTTP."""
    from tests.test_conversation_models import make_quote_chain

    return make_quote_chain(db, company)


class TestAcceptGuard:
    def test_accept_is_refused_when_a_deposit_is_required(self, client, db, live) -> None:
        """Otherwise /accept would be a free way to book without paying."""
        resp = client.post(f"/api/v1/public/quotes/{live['token']}/accept")
        assert resp.status_code == 409
        assert "deposit" in resp.json()["error"]["message"]
        assert db.scalar(select(func.count()).select_from(Booking)) == 0
        db.refresh(live["quote"])
        assert live["quote"].status is QuoteStatus.SENT

    def test_accept_still_works_without_a_deposit(self, client, db, company) -> None:
        token = submit(client)["quote"]["public_token"]
        resp = client.post(f"/api/v1/public/quotes/{token}/accept")
        assert resp.status_code == 200
        assert db.scalar(select(func.count()).select_from(Booking)) == 1


class TestCheckout:
    def test_amount_comes_from_the_database(self, client, db, provider, live) -> None:
        body = checkout(client, live["token"]).json()
        assert body["amount_cents"] == 20_000
        # The provider decides where the customer goes; we only require that it is the
        # return URL we asked for, so the redirect lands back on our own page.
        assert body["checkout_url"].endswith(f"/quote/{live['token']}/paid")

        payment = db.scalar(select(Payment))
        assert payment.amount_cents == 20_000
        assert payment.status is PaymentStatus.PENDING

    def test_the_browser_cannot_set_the_amount(self, client, db, provider, live) -> None:
        """The endpoint takes no body; anything sent is ignored, not honoured."""
        client.post(
            f"/api/v1/public/quotes/{live['token']}/checkout",
            json={"amount_cents": 1, "deposit_cents": 1, "currency": "XXX"},
        )
        payment = db.scalar(select(Payment))
        assert payment is None or payment.amount_cents == 20_000

    def test_checkout_creates_no_booking(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        assert db.scalar(select(func.count()).select_from(Booking)) == 0
        db.refresh(live["quote"])
        assert live["quote"].status is QuoteStatus.SENT

    def test_checkout_is_refused_without_a_deposit(self, client, db, provider, company) -> None:
        token = submit(client)["quote"]["public_token"]
        assert checkout(client, token).status_code == 409

    def test_checkout_is_refused_for_an_accepted_quote(self, client, db, provider, live) -> None:
        live["quote"].status = QuoteStatus.ACCEPTED
        db.commit()
        assert checkout(client, live["token"]).status_code == 409

    def test_unknown_token_is_404(self, client, provider, company) -> None:
        assert checkout(client, "nope").status_code == 404


class TestWebhookAuthority:
    def test_a_verified_success_creates_the_booking(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        session_id = db.scalar(select(Payment)).provider_session_id

        assert send_event(client, provider, success_event(session_id)).status_code == 200

        payment = db.scalar(select(Payment))
        assert payment.status is PaymentStatus.SUCCEEDED
        assert payment.succeeded_at is not None
        assert payment.provider_payment_intent_id == "pi_123"

        booking = db.scalar(select(Booking))
        assert booking is not None
        db.refresh(live["quote"])
        assert live["quote"].status is QuoteStatus.ACCEPTED

    def test_an_invalid_signature_changes_nothing(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        session_id = db.scalar(select(Payment)).provider_session_id

        resp = send_event(
            client, provider, success_event(session_id), signature="not-a-real-signature"
        )
        assert resp.status_code == 401
        assert db.scalar(select(func.count()).select_from(Booking)) == 0
        assert db.scalar(select(Payment)).status is PaymentStatus.PENDING
        assert db.scalar(select(func.count()).select_from(ProcessedWebhookEvent)) == 0

    def test_a_missing_signature_changes_nothing(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        payload = json.dumps(success_event(db.scalar(select(Payment)).provider_session_id))
        resp = client.post(WEBHOOK_URL, content=payload.encode())
        assert resp.status_code == 401
        assert db.scalar(select(func.count()).select_from(Booking)) == 0

    def test_a_duplicate_webhook_creates_one_booking(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        session_id = db.scalar(select(Payment)).provider_session_id
        event = success_event(session_id)

        for _ in range(4):
            assert send_event(client, provider, event).status_code == 200

        assert db.scalar(select(func.count()).select_from(Booking)) == 1
        assert db.scalar(select(func.count()).select_from(ProcessedWebhookEvent)) == 1

    def test_a_second_distinct_event_for_the_same_session_is_safe(
        self, client, db, provider, live
    ) -> None:
        """Providers emit several event types per session; only the first books."""
        checkout(client, live["token"])
        session_id = db.scalar(select(Payment)).provider_session_id

        send_event(client, provider, success_event(session_id, event_id="evt_a"))
        send_event(client, provider, success_event(session_id, event_id="evt_b"))

        assert db.scalar(select(func.count()).select_from(Booking)) == 1
        assert db.scalar(select(func.count()).select_from(ProcessedWebhookEvent)) == 2

    def test_a_failed_payment_does_not_book(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        session_id = db.scalar(select(Payment)).provider_session_id

        send_event(
            client,
            provider,
            {"id": "evt_f", "kind": "failed", "session_id": session_id},
        )
        assert db.scalar(select(Payment)).status is PaymentStatus.FAILED
        assert db.scalar(select(func.count()).select_from(Booking)) == 0
        db.refresh(live["quote"])
        assert live["quote"].status is QuoteStatus.SENT

    def test_an_expired_session_does_not_book(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        session_id = db.scalar(select(Payment)).provider_session_id
        send_event(client, provider, {"id": "evt_x", "kind": "expired", "session_id": session_id})
        assert db.scalar(select(Payment)).status is PaymentStatus.CANCELLED
        assert db.scalar(select(func.count()).select_from(Booking)) == 0

    def test_an_event_for_an_unknown_session_is_ignored(self, client, db, provider, live) -> None:
        resp = send_event(client, provider, success_event("cs_never_created"))
        assert resp.status_code == 200
        assert db.scalar(select(func.count()).select_from(Booking)) == 0

    def test_the_response_reveals_nothing(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        session_id = db.scalar(select(Payment)).provider_session_id
        body = send_event(client, provider, success_event(session_id)).json()
        assert body == {"received": True}


class TestRedirectProvesNothing:
    def test_status_is_unconfirmed_until_the_webhook_lands(
        self, client, db, provider, live
    ) -> None:
        """A customer who lands on the success page without paying sees the truth."""
        checkout(client, live["token"])
        body = client.get(f"/api/v1/public/quotes/{live['token']}/payment-status").json()
        assert body["status"] == "pending"
        assert body["booking_confirmed"] is False

    def test_status_flips_only_after_a_verified_event(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        session_id = db.scalar(select(Payment)).provider_session_id
        send_event(client, provider, success_event(session_id))

        body = client.get(f"/api/v1/public/quotes/{live['token']}/payment-status").json()
        assert body["status"] == "succeeded"
        assert body["booking_confirmed"] is True

    def test_visiting_the_status_endpoint_never_writes(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        for _ in range(5):
            client.get(f"/api/v1/public/quotes/{live['token']}/payment-status")
        assert db.scalar(select(func.count()).select_from(Booking)) == 0
        assert db.scalar(select(Payment)).status is PaymentStatus.PENDING

    def test_status_for_a_quote_with_no_payment(self, client, db, provider, company) -> None:
        token = submit(client)["quote"]["public_token"]
        body = client.get(f"/api/v1/public/quotes/{token}/payment-status").json()
        assert body["status"] == "none"
        assert body["booking_confirmed"] is False


class TestTenantScope:
    def test_a_payment_belongs_to_the_quotes_company(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        payment = db.scalar(select(Payment))
        quote = db.get(Quote, payment.quote_id)
        assert payment.company_id == quote.company_id == live["company"].id

    def test_webhook_never_leaks_identifiers(self, client, db, provider, live) -> None:
        checkout(client, live["token"])
        session_id = db.scalar(select(Payment)).provider_session_id
        raw = send_event(client, provider, success_event(session_id)).text
        for secret in (str(live["quote"].id), str(live["company"].id), live["token"]):
            assert secret not in raw
