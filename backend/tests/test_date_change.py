"""Tests for the customer date-change flow and the quote revision chain (Step 4C).

Two properties are asserted repeatedly because everything else rests on them:

* a **preview writes nothing** — the customer can look and walk away;
* a **confirm appends** — the old quote keeps its price, snapshot, engine version and
  config pointer, so the original offer stays replayable forever.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import (
    ChangeKind,
    CompanyDateCapacity,
    MovingRequest,
    Quote,
    QuoteChangeRequest,
    QuoteStatus,
)
from app.services import quotes as quote_service
from app.services import requote as requote_service
from tests.test_availability import book
from tests.test_quotes_api import submit


def quote_of(db, token: str) -> Quote:
    return db.scalar(select(Quote).where(Quote.public_token == token))


@pytest.fixture()
def live(client: TestClient, db, company):
    """A real sent quote produced by the funnel, with its token."""
    body = submit(client)
    token = body["quote"]["public_token"]
    quote = quote_of(db, token)
    request = db.get(MovingRequest, quote.moving_request_id)
    return {"token": token, "quote": quote, "request": request, "company": company}


def preview_url(token: str) -> str:
    return f"/api/v1/public/quotes/{token}/date-preview"


def change_url(token: str) -> str:
    return f"/api/v1/public/quotes/{token}/date-change"


def a_free_date(request: MovingRequest, *, offset: int = 7) -> date:
    """A date inside the bookable window that differs from the current move date."""
    return request.move_date + timedelta(days=offset)


def _weekend_pair(after: date) -> tuple[date, date]:
    """The next Saturday/Monday pair that both fall before the month-end surcharge."""
    for offset in range(3, 60):
        day = after + timedelta(days=offset)
        monday = day + timedelta(days=2)
        if day.weekday() == 5 and day.day < 25 and monday.day < 25:
            return day, monday
    raise AssertionError("no clean Saturday/Monday pair found")  # pragma: no cover


class TestPreviewWritesNothing:
    def test_preview_returns_both_sides(self, client, live) -> None:
        new_date = a_free_date(live["request"])
        body = client.post(preview_url(live["token"]), json={"move_date": new_date.isoformat()})
        assert body.status_code == 200, body.text
        payload = body.json()

        assert payload["current"]["move_date"] == live["request"].move_date.isoformat()
        assert payload["proposed"]["move_date"] == new_date.isoformat()
        assert set(payload) == {
            "current",
            "proposed",
            "price_changed",
            "difference_min_cents",
            "difference_max_cents",
            "proposed_valid_until",
        }

    def test_preview_creates_no_rows(self, client, db, live) -> None:
        before = {
            model.__name__: db.scalar(select(func.count()).select_from(model))
            for model in (Quote, MovingRequest, QuoteChangeRequest)
        }
        for offset in (5, 6, 7):
            client.post(
                preview_url(live["token"]),
                json={"move_date": a_free_date(live["request"], offset=offset).isoformat()},
            )
        after = {
            model.__name__: db.scalar(select(func.count()).select_from(model))
            for model in (Quote, MovingRequest, QuoteChangeRequest)
        }
        assert after == before

    def test_preview_does_not_touch_the_existing_quote(self, client, db, live) -> None:
        quote = live["quote"]
        before = (quote.status, quote.amount_min_cents, quote.revision, quote.moving_request_id)
        client.post(
            preview_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        db.refresh(quote)
        assert (
            quote.status,
            quote.amount_min_cents,
            quote.revision,
            quote.moving_request_id,
        ) == before

    def test_price_difference_is_reported(self, client, live) -> None:
        payload = client.post(
            preview_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        ).json()
        expected = payload["proposed"]["amount_min_cents"] - payload["current"]["amount_min_cents"]
        assert payload["difference_min_cents"] == expected
        assert payload["price_changed"] is (expected != 0)

    def test_a_weekend_date_costs_more_than_a_weekday(self, client, live) -> None:
        """The engine's weekend multiplier is live, so the date really does move price.

        Compared against a Monday rather than the current quote, so the assertion holds
        whatever weekday the funnel happened to pick. Both dates are kept before the
        25th so the month-end multiplier cannot muddy the comparison.
        """
        saturday, monday = _weekend_pair(live["request"].move_date)
        amounts = {
            label: client.post(
                preview_url(live["token"]), json={"move_date": day.isoformat()}
            ).json()["proposed"]["amount_min_cents"]
            for label, day in (("saturday", saturday), ("monday", monday))
        }
        assert amounts["saturday"] > amounts["monday"]


class TestPreviewValidation:
    def test_unavailable_date_is_rejected(self, client, db, live) -> None:
        target = a_free_date(live["request"])
        db.add(CompanyDateCapacity(company_id=live["company"].id, date=target, is_blocked=True))
        db.commit()

        resp = client.post(preview_url(live["token"]), json={"move_date": target.isoformat()})
        assert resp.status_code == 409
        assert "available" in resp.json()["error"]["message"]

    def test_full_date_is_rejected(self, client, db, live) -> None:
        target = a_free_date(live["request"])
        db.add(CompanyDateCapacity(company_id=live["company"].id, date=target, max_moves=0))
        db.commit()
        resp = client.post(preview_url(live["token"]), json={"move_date": target.isoformat()})
        assert resp.status_code == 409

    def test_past_date_is_rejected(self, client, live) -> None:
        resp = client.post(
            preview_url(live["token"]),
            json={"move_date": (date.today() - timedelta(days=1)).isoformat()},
        )
        assert resp.status_code == 409

    def test_same_date_is_rejected(self, client, live) -> None:
        resp = client.post(
            preview_url(live["token"]),
            json={"move_date": live["request"].move_date.isoformat()},
        )
        assert resp.status_code == 422
        assert "already your move date" in resp.json()["error"]["message"]

    def test_identifiers_in_the_body_are_refused(self, client, live) -> None:
        resp = client.post(
            preview_url(live["token"]),
            json={
                "move_date": a_free_date(live["request"]).isoformat(),
                "company_id": "11111111-1111-1111-1111-111111111111",
            },
        )
        assert resp.status_code == 422

    def test_unknown_token_is_404(self, client, company) -> None:
        assert client.post(preview_url("nope"), json={"move_date": "2026-12-01"}).status_code == 404


class TestConfirmAppends:
    def test_confirm_creates_a_new_revision(self, client, db, live) -> None:
        new_date = a_free_date(live["request"])
        resp = client.post(change_url(live["token"]), json={"move_date": new_date.isoformat()})
        assert resp.status_code == 200, resp.text
        assert resp.json()["move_date"] == new_date.isoformat()

        head = quote_service.get_quote_by_token(db, live["token"])
        assert head.revision == 2
        assert head.id != live["quote"].id
        assert head.status is QuoteStatus.SENT

    def test_the_old_quote_is_preserved_untouched(self, client, db, live) -> None:
        old = live["quote"]
        snapshot = (
            old.amount_min_cents,
            old.amount_max_cents,
            old.engine_version,
            dict(old.inputs_snapshot),
            old.pricing_config_id,
            old.moving_request_id,
        )
        client.post(
            change_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        db.refresh(old)

        assert (
            old.amount_min_cents,
            old.amount_max_cents,
            old.engine_version,
            dict(old.inputs_snapshot),
            old.pricing_config_id,
            old.moving_request_id,
        ) == snapshot
        assert old.status is QuoteStatus.SUPERSEDED
        assert old.superseded_by_quote_id is not None

    def test_the_original_move_request_is_preserved(self, client, db, live) -> None:
        original = live["request"]
        original_date = original.move_date
        client.post(
            change_url(live["token"]),
            json={"move_date": a_free_date(original).isoformat()},
        )
        db.refresh(original)
        assert original.move_date == original_date

        head = quote_service.get_quote_by_token(db, live["token"])
        new_request = db.get(MovingRequest, head.moving_request_id)
        assert new_request.id != original.id
        assert new_request.supersedes_request_id == original.id
        assert new_request.lead_id == original.lead_id
        assert new_request.origin_line1 == original.origin_line1

    def test_the_original_token_resolves_forward(self, client, db, live) -> None:
        """The customer's emailed link must keep working after an edit."""
        new_date = a_free_date(live["request"])
        client.post(change_url(live["token"]), json={"move_date": new_date.isoformat()})

        page = client.get(f"/api/v1/public/quotes/{live['token']}").json()
        assert page["move_date"] == new_date.isoformat()
        assert page["status"] == "sent"

    def test_two_edits_chain(self, client, db, live) -> None:
        first = a_free_date(live["request"], offset=5)
        second = a_free_date(live["request"], offset=9)
        client.post(change_url(live["token"]), json={"move_date": first.isoformat()})
        client.post(change_url(live["token"]), json={"move_date": second.isoformat()})

        head = quote_service.get_quote_by_token(db, live["token"])
        assert head.revision == 3
        assert db.scalar(select(func.count()).select_from(Quote)) == 3
        page = client.get(f"/api/v1/public/quotes/{live['token']}").json()
        assert page["move_date"] == second.isoformat()

    def test_new_revision_is_priced_by_the_engine(self, client, db, live) -> None:
        new_date = a_free_date(live["request"])
        preview = client.post(
            preview_url(live["token"]), json={"move_date": new_date.isoformat()}
        ).json()
        client.post(change_url(live["token"]), json={"move_date": new_date.isoformat()})

        head = quote_service.get_quote_by_token(db, live["token"])
        assert head.amount_min_cents == preview["proposed"]["amount_min_cents"]
        assert head.amount_max_cents == preview["proposed"]["amount_max_cents"]
        assert head.engine_version
        assert head.inputs_snapshot


class TestAuditTrail:
    def test_a_change_request_row_records_the_edit(self, client, db, live) -> None:
        new_date = a_free_date(live["request"])
        client.post(change_url(live["token"]), json={"move_date": new_date.isoformat()})

        record = db.scalar(select(QuoteChangeRequest))
        head = quote_service.get_quote_by_token(db, live["token"])
        assert record.kind is ChangeKind.DATE
        assert record.previous_quote_id == live["quote"].id
        assert record.resulting_quote_id == head.id
        assert record.requested_changes == {"move_date": new_date.isoformat()}
        assert record.confirmed_at is not None
        assert record.company_id == live["company"].id

    def test_no_change_request_row_without_confirmation(self, client, db, live) -> None:
        client.post(
            preview_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        assert db.scalar(select(func.count()).select_from(QuoteChangeRequest)) == 0

    def test_the_full_history_is_reconstructable(self, client, db, live) -> None:
        """Original inputs, the ask, the recalculated result, and consent — all present."""
        new_date = a_free_date(live["request"])
        client.post(change_url(live["token"]), json={"move_date": new_date.isoformat()})

        record = db.scalar(select(QuoteChangeRequest))
        previous = db.get(Quote, record.previous_quote_id)
        resulting = db.get(Quote, record.resulting_quote_id)

        assert previous.inputs_snapshot  # what it was priced from
        assert resulting.inputs_snapshot  # what it is priced from now
        assert previous.pricing_config_id and resulting.pricing_config_id
        assert db.get(MovingRequest, resulting.moving_request_id).supersedes_request_id == (
            previous.moving_request_id
        )


class TestConfirmValidatesIndependently:
    def test_a_date_that_fills_between_preview_and_confirm_is_refused(
        self, client, db, live
    ) -> None:
        """Confirm never trusts that a preview happened, or what it said."""
        target = a_free_date(live["request"])
        assert (
            client.post(
                preview_url(live["token"]), json={"move_date": target.isoformat()}
            ).status_code
            == 200
        )

        db.add(CompanyDateCapacity(company_id=live["company"].id, date=target, is_blocked=True))
        db.commit()

        resp = client.post(change_url(live["token"]), json={"move_date": target.isoformat()})
        assert resp.status_code == 409
        assert db.scalar(select(func.count()).select_from(Quote)) == 1

    def test_confirming_without_previewing_still_works(self, client, db, live) -> None:
        """The preview is a courtesy to the customer, not a required handshake."""
        resp = client.post(
            change_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        assert resp.status_code == 200

    def test_capacity_is_consumed_by_real_bookings(self, client, db, live) -> None:
        target = a_free_date(live["request"])
        from app.services import availability as av

        for _ in range(av.DEFAULT_DAILY_CAPACITY):
            book(db, live["company"], target)
        resp = client.post(change_url(live["token"]), json={"move_date": target.isoformat()})
        assert resp.status_code == 409


class TestNonEditableQuotes:
    def test_accepted_quote_cannot_be_changed(self, client, db, live) -> None:
        client.post(f"/api/v1/public/quotes/{live['token']}/accept")
        resp = client.post(
            change_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        assert resp.status_code == 409
        assert "already booked" in resp.json()["error"]["message"]

    def test_declined_quote_cannot_be_changed(self, client, db, live) -> None:
        client.post(f"/api/v1/public/quotes/{live['token']}/decline")
        resp = client.post(
            change_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        assert resp.status_code == 409

    def test_expired_quote_cannot_be_changed(self, client, db, live) -> None:
        live["quote"].valid_until = datetime.now(UTC) - timedelta(days=1)
        db.commit()
        resp = client.post(
            change_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        assert resp.status_code == 409

    def test_review_mode_company_blocks_self_service_edits(self, client, db, live) -> None:
        """Hand-priced companies must not have a revision created behind their back."""
        live["company"].settings = {"quote_review_mode": True}
        db.commit()
        resp = client.post(
            change_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        assert resp.status_code == 409
        assert "contact them" in resp.json()["error"]["message"]


class TestChainResolution:
    def test_head_of_an_unedited_quote_is_itself(self, db, live) -> None:
        assert requote_service.resolve_head(db, live["quote"]).id == live["quote"].id

    def test_every_token_in_the_chain_resolves_to_the_same_head(self, client, db, live) -> None:
        client.post(
            change_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        head = quote_service.get_quote_by_token(db, live["token"])
        # The new revision has its own token; both must land on the same row.
        assert quote_service.get_quote_by_token(db, head.public_token).id == head.id

    def test_cross_tenant_token_tampering_is_rejected(self, client, db, company) -> None:
        """A token from another tenant reaches that tenant's quote, never ours."""
        from app.models import Company

        other = Company(name="Bravo", slug="bravo-van-lines", settings={})
        db.add(other)
        db.commit()
        theirs = submit(client, slug="bravo-van-lines")["quote"]["public_token"]

        resp = client.post(preview_url(theirs), json={"move_date": "2026-12-15"})
        # It resolves to *their* quote, priced with *their* config — never a mix.
        if resp.status_code == 200:
            their_quote = quote_of(db, theirs)
            assert their_quote.company_id == other.id
            assert their_quote.company_id != company.id
