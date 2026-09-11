"""Tests for the customer edit-move / requote flow (Step 4D).

Same two guarantees as the date flow — preview writes nothing, confirm appends — plus
the requote-specific one: an edited price is always a price the deterministic engine
would have produced for those inputs, never an adjustment applied on top.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import (
    ChangeKind,
    HomeSize,
    MovingRequest,
    Quote,
    QuoteChangeRequest,
    QuoteStatus,
)
from app.pricing import MoveSpec, RuleBasedEngine
from app.services import pricing as pricing_service
from app.services import quotes as quote_service
from tests.test_date_change import a_free_date, quote_of
from tests.test_quotes_api import submit


def details_url(token: str) -> str:
    return f"/api/v1/public/quotes/{token}/move-details"


def preview_url(token: str) -> str:
    return f"/api/v1/public/quotes/{token}/edit-preview"


def edit_url(token: str) -> str:
    return f"/api/v1/public/quotes/{token}/edit"


@pytest.fixture()
def live(client, db, company):
    body = submit(client)
    token = body["quote"]["public_token"]
    quote = quote_of(db, token)
    return {
        "token": token,
        "quote": quote,
        "request": db.get(MovingRequest, quote.moving_request_id),
        "company": company,
    }


class TestMoveDetails:
    def test_returns_the_priced_inputs(self, client, live) -> None:
        body = client.get(details_url(live["token"])).json()
        assert body["home_size"] == live["request"].home_size.value
        assert body["origin_floor"] == live["request"].origin_floor
        assert body["move_date"] == live["request"].move_date.isoformat()

    def test_street_addresses_are_not_exposed(self, client, live) -> None:
        """Matches the quote page's existing surface: city only, never line1."""
        raw = client.get(details_url(live["token"])).text
        assert live["request"].origin_line1 not in raw
        assert "line1" not in raw

    def test_no_identifiers_are_exposed(self, client, live) -> None:
        raw = client.get(details_url(live["token"])).text
        for secret in (str(live["quote"].id), str(live["company"].id), str(live["request"].id)):
            assert secret not in raw


class TestEditPreview:
    def test_home_size_change_reprices(self, client, live) -> None:
        body = client.post(preview_url(live["token"]), json={"home_size": "4br"}).json()
        assert body["proposed"]["amount_min_cents"] != body["current"]["amount_min_cents"]
        assert body["price_changed"] is True

    def test_packing_service_change_reprices(self, client, live) -> None:
        body = client.post(preview_url(live["token"]), json={"packing_service": "full"}).json()
        assert body["proposed"]["amount_min_cents"] > body["current"]["amount_min_cents"]

    def test_access_details_change_reprices(self, client, live) -> None:
        body = client.post(
            preview_url(live["token"]),
            json={"origin": {"floor": 4, "has_elevator": False, "stairs_flights": 3}},
        ).json()
        assert body["proposed"]["estimated_hours"] > body["current"]["estimated_hours"]

    def test_special_items_can_be_changed(self, client, live) -> None:
        """The funnel already submits ["piano"], so adding to it is the real edit."""
        resp = client.post(
            preview_url(live["token"]), json={"special_items": ["piano", "gun safe"]}
        )
        assert resp.status_code == 200

    def test_special_items_can_be_cleared(self, client, live) -> None:
        assert (
            client.post(preview_url(live["token"]), json={"special_items": []}).status_code == 200
        )

    def test_combined_edits_are_priced_together(self, client, live) -> None:
        body = client.post(
            preview_url(live["token"]),
            json={
                "home_size": "4br",
                "packing_service": "full",
                "move_date": a_free_date(live["request"]).isoformat(),
            },
        ).json()
        assert body["price_changed"] is True

    def test_preview_writes_nothing(self, client, db, live) -> None:
        before = {
            m.__name__: db.scalar(select(func.count()).select_from(m))
            for m in (Quote, MovingRequest, QuoteChangeRequest)
        }
        client.post(preview_url(live["token"]), json={"home_size": "4br"})
        client.post(preview_url(live["token"]), json={"packing_service": "full"})
        after = {
            m.__name__: db.scalar(select(func.count()).select_from(m))
            for m in (Quote, MovingRequest, QuoteChangeRequest)
        }
        assert after == before

    def test_the_preview_matches_the_engine_exactly(self, client, db, live) -> None:
        """The customer is shown the engine's own number, not a derived one."""
        body = client.post(preview_url(live["token"]), json={"home_size": "4br"}).json()

        request = live["request"]
        spec = MoveSpec.from_moving_request(request).model_copy(
            update={"home_size": HomeSize.FOUR_BR}
        )
        config_row = pricing_service.get_active_config_row(db, live["company"].id)
        expected = RuleBasedEngine().estimate(spec, pricing_service.load_config(config_row))

        assert body["proposed"]["amount_min_cents"] == expected.amount_min_cents
        assert body["proposed"]["amount_max_cents"] == expected.amount_max_cents


class TestEditValidation:
    def test_unknown_field_is_rejected(self, client, live) -> None:
        resp = client.post(preview_url(live["token"]), json={"amount_min_cents": 1})
        assert resp.status_code == 422

    def test_price_cannot_be_supplied_by_the_customer(self, client, db, live) -> None:
        """The customer names inputs, never outputs."""
        for body in ({"total_cents": 1}, {"amount_min_cents": 1}, {"crew_size": 1}):
            assert client.post(edit_url(live["token"]), json=body).status_code == 422
        assert db.scalar(select(func.count()).select_from(Quote)) == 1

    def test_identifiers_are_rejected(self, client, live) -> None:
        resp = client.post(
            edit_url(live["token"]),
            json={"home_size": "4br", "quote_id": "11111111-1111-1111-1111-111111111111"},
        )
        assert resp.status_code == 422

    def test_invalid_enum_is_rejected(self, client, live) -> None:
        assert (
            client.post(preview_url(live["token"]), json={"home_size": "castle"}).status_code == 422
        )

    def test_out_of_range_access_is_rejected(self, client, live) -> None:
        for bad in ({"floor": 0}, {"floor": 500}, {"stairs_flights": -1}, {"stairs_flights": 99}):
            resp = client.post(preview_url(live["token"]), json={"origin": bad})
            assert resp.status_code == 422, bad

    def test_too_many_special_items_rejected(self, client, live) -> None:
        resp = client.post(
            preview_url(live["token"]), json={"special_items": [f"item{i}" for i in range(21)]}
        )
        assert resp.status_code == 422

    def test_empty_edit_is_rejected(self, client, live) -> None:
        assert client.post(preview_url(live["token"]), json={}).status_code == 422

    def test_resubmitting_unchanged_values_is_rejected(self, client, db, live) -> None:
        """An edit form posts every field; identical values must not create a revision."""
        current = client.get(details_url(live["token"])).json()
        resp = client.post(
            edit_url(live["token"]),
            json={
                "home_size": current["home_size"],
                "packing_service": current["packing_service"],
                "move_date": current["move_date"],
                "special_items": current["special_items"],
            },
        )
        assert resp.status_code == 422
        assert "Nothing was changed" in resp.json()["error"]["message"]
        assert db.scalar(select(func.count()).select_from(Quote)) == 1

    def test_a_date_inside_an_edit_still_checks_availability(self, client, db, live) -> None:
        from app.models import CompanyDateCapacity

        target = a_free_date(live["request"])
        db.add(CompanyDateCapacity(company_id=live["company"].id, date=target, is_blocked=True))
        db.commit()
        resp = client.post(
            edit_url(live["token"]),
            json={"home_size": "4br", "move_date": target.isoformat()},
        )
        assert resp.status_code == 409


class TestEditConfirm:
    def test_confirm_creates_a_revision_and_preserves_the_old(self, client, db, live) -> None:
        old = live["quote"]
        old_amount, old_snapshot = old.amount_min_cents, dict(old.inputs_snapshot)

        resp = client.post(edit_url(live["token"]), json={"home_size": "4br"})
        assert resp.status_code == 200

        db.refresh(old)
        assert old.status is QuoteStatus.SUPERSEDED
        assert old.amount_min_cents == old_amount
        assert dict(old.inputs_snapshot) == old_snapshot

        head = quote_service.get_quote_by_token(db, live["token"])
        assert head.revision == 2
        assert db.get(MovingRequest, head.moving_request_id).home_size.value == "4br"

    def test_unedited_fields_carry_forward(self, client, db, live) -> None:
        original = live["request"]
        client.post(edit_url(live["token"]), json={"home_size": "4br"})

        head = quote_service.get_quote_by_token(db, live["token"])
        new_request = db.get(MovingRequest, head.moving_request_id)
        assert new_request.origin_line1 == original.origin_line1
        assert new_request.destination_zip == original.destination_zip
        assert new_request.move_date == original.move_date
        assert new_request.distance_miles == original.distance_miles
        assert new_request.lead_id == original.lead_id

    def test_confirmed_price_equals_the_previewed_price(self, client, db, live) -> None:
        edit = {"home_size": "4br", "packing_service": "full"}
        preview = client.post(preview_url(live["token"]), json=edit).json()
        client.post(edit_url(live["token"]), json=edit)

        head = quote_service.get_quote_by_token(db, live["token"])
        assert head.amount_min_cents == preview["proposed"]["amount_min_cents"]
        assert head.amount_max_cents == preview["proposed"]["amount_max_cents"]

    def test_audit_row_records_a_details_change(self, client, db, live) -> None:
        client.post(edit_url(live["token"]), json={"home_size": "4br"})
        record = db.scalar(select(QuoteChangeRequest))
        assert record.kind is ChangeKind.DETAILS
        assert record.requested_changes == {"home_size": "4br"}

    def test_a_date_only_edit_is_recorded_as_a_date_change(self, client, db, live) -> None:
        client.post(
            edit_url(live["token"]),
            json={"move_date": a_free_date(live["request"]).isoformat()},
        )
        assert db.scalar(select(QuoteChangeRequest)).kind is ChangeKind.DATE

    def test_the_original_token_shows_the_edited_move(self, client, db, live) -> None:
        client.post(edit_url(live["token"]), json={"home_size": "4br"})
        page = client.get(f"/api/v1/public/quotes/{live['token']}").json()
        assert page["home_size"] == "4br"

    def test_provenance_chain_survives_two_edits(self, client, db, live) -> None:
        client.post(edit_url(live["token"]), json={"home_size": "4br"})
        client.post(edit_url(live["token"]), json={"packing_service": "full"})

        head = quote_service.get_quote_by_token(db, live["token"])
        assert head.revision == 3
        # Every revision keeps its own config pointer and engine version.
        quotes = db.scalars(select(Quote).order_by(Quote.revision)).all()
        assert [q.revision for q in quotes] == [1, 2, 3]
        assert all(q.pricing_config_id and q.engine_version and q.inputs_snapshot for q in quotes)
        # And the request chain mirrors it.
        requests = {q.revision: db.get(MovingRequest, q.moving_request_id) for q in quotes}
        assert requests[2].supersedes_request_id == requests[1].id
        assert requests[3].supersedes_request_id == requests[2].id

    def test_accepted_quote_cannot_be_edited(self, client, db, live) -> None:
        client.post(f"/api/v1/public/quotes/{live['token']}/accept")
        resp = client.post(edit_url(live["token"]), json={"home_size": "4br"})
        assert resp.status_code == 409


class TestCrossTenant:
    def test_editing_with_another_tenants_token_never_touches_ours(
        self, client, db, company
    ) -> None:
        from app.models import Company

        other = Company(name="Bravo", slug="bravo-van-lines", settings={})
        db.add(other)
        db.commit()
        mine = submit(client)["quote"]["public_token"]
        theirs = submit(client, slug="bravo-van-lines")["quote"]["public_token"]

        client.post(edit_url(theirs), json={"home_size": "4br"})

        my_quote = quote_of(db, mine)
        assert my_quote.status is QuoteStatus.SENT
        assert my_quote.superseded_by_quote_id is None
        assert db.get(MovingRequest, my_quote.moving_request_id).home_size.value != "4br"

    def test_edited_revision_stays_in_its_own_tenant(self, client, db, company) -> None:
        from app.models import Company

        other = Company(name="Bravo", slug="bravo-van-lines", settings={})
        db.add(other)
        db.commit()
        theirs = submit(client, slug="bravo-van-lines")["quote"]["public_token"]

        client.post(edit_url(theirs), json={"home_size": "4br"})
        head = quote_service.get_quote_by_token(db, theirs)
        assert head.company_id == other.id
        assert db.get(MovingRequest, head.moving_request_id).company_id == other.id
