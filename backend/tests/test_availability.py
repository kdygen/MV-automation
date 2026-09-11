"""Tests for the availability model, service, and both APIs (Step 4A).

Availability is derived, never stored, so most of these assert that a change somewhere
else — a new booking, a cancelled booking, an owner override — immediately changes what
the calendar says, without any sync step.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ConflictError, ValidationError
from app.models import (
    Booking,
    BookingStatus,
    Company,
    CompanyDateCapacity,
    HomeSize,
    Lead,
    MovingRequest,
    Quote,
    QuoteStatus,
    User,
    UserRole,
)
from app.services import availability as av
from app.services import pricing as pricing_service
from tests.conftest import mint_token
from tests.test_conversation_models import make_quote_chain

TODAY = date(2026, 9, 10)
SOON = TODAY + timedelta(days=10)  # comfortably inside the bookable window


def book(db, company: Company, day: date, *, status=BookingStatus.CONFIRMED) -> Booking:
    """A booking on ``day``, with the minimum chain the FKs require.

    Written locally rather than reusing ``make_quote_chain`` because that helper always
    seeds pricing config v1, so it cannot run twice for one company — and filling a
    date to capacity needs several bookings for the same company.
    """
    lead = Lead(company_id=company.id, name="Bob", email=f"{uuid.uuid4().hex[:8]}@example.test")
    db.add(lead)
    db.flush()
    request = MovingRequest(
        company_id=company.id,
        lead_id=lead.id,
        origin_line1="12 Elm St",
        origin_city="Springfield",
        origin_state="IL",
        origin_zip="62701",
        destination_line1="99 Oak Ave",
        destination_city="Chatham",
        destination_state="IL",
        destination_zip="62629",
        move_date=day,
        home_size=HomeSize.TWO_BR,
        special_items=[],
        distance_miles=12.0,
        raw_payload={},
    )
    db.add(request)
    db.flush()
    quote = Quote(
        company_id=company.id,
        moving_request_id=request.id,
        pricing_config_id=pricing_service.ensure_active_config(db, company.id).id,
        status=QuoteStatus.ACCEPTED,
        amount_min_cents=100_000,
        amount_max_cents=130_000,
        total_cents=115_000,
        estimated_hours=6.0,
        crew_size=3,
        engine_version="rules-v1.0",
        line_items=[],
        inputs_snapshot={},
        public_token=uuid.uuid4().hex,
        valid_until=datetime.now(UTC) + timedelta(days=14),
    )
    db.add(quote)
    db.flush()
    booking = Booking(
        company_id=company.id,
        quote_id=quote.id,
        scheduled_date=day,
        crew_size=3,
        status=status,
    )
    db.add(booking)
    db.commit()
    return booking


def override(db, company: Company, day: date, **kwargs) -> CompanyDateCapacity:
    row = CompanyDateCapacity(company_id=company.id, date=day, **kwargs)
    db.add(row)
    db.commit()
    return row


def day_of(db, company: Company, day: date) -> av.DayAvailability:
    return av.day_availability(db, company, day, today=TODAY)


class TestDefaults:
    def test_an_empty_table_means_normal_capacity(self, db, company) -> None:
        """No rows must mean "open", not "closed" — the safe default for a new tenant."""
        assert day_of(db, company, SOON).is_available is True

    def test_lead_time_blocks_dates_that_are_too_soon(self, db, company) -> None:
        assert day_of(db, company, TODAY).reason == av.REASON_TOO_SOON
        assert day_of(db, company, TODAY + timedelta(days=1)).reason == av.REASON_TOO_SOON
        assert day_of(db, company, TODAY + timedelta(days=2)).is_available is True

    def test_horizon_blocks_dates_too_far_ahead(self, db, company) -> None:
        far = TODAY + timedelta(days=av.DEFAULT_BOOKING_HORIZON_DAYS + 1)
        assert day_of(db, company, far).reason == av.REASON_TOO_FAR
        edge = TODAY + timedelta(days=av.DEFAULT_BOOKING_HORIZON_DAYS)
        assert day_of(db, company, edge).is_available is True

    def test_settings_override_the_defaults(self, db, company) -> None:
        company.settings = {"lead_time_days": 0, "booking_horizon_days": 3}
        db.commit()
        assert day_of(db, company, TODAY).is_available is True
        assert day_of(db, company, TODAY + timedelta(days=4)).reason == av.REASON_TOO_FAR


class TestCapacity:
    def test_a_date_fills_up_at_capacity(self, db, company) -> None:
        for _ in range(av.DEFAULT_DAILY_CAPACITY):
            book(db, company, SOON)
        assert day_of(db, company, SOON).reason == av.REASON_FULL

    def test_below_capacity_stays_available(self, db, company) -> None:
        book(db, company, SOON)
        assert day_of(db, company, SOON).is_available is True

    def test_cancelled_bookings_free_the_slot(self, db, company) -> None:
        """Capacity is derived from live bookings, so a cancellation reopens the date."""
        bookings = [book(db, company, SOON) for _ in range(av.DEFAULT_DAILY_CAPACITY)]
        assert day_of(db, company, SOON).is_available is False

        bookings[0].status = BookingStatus.CANCELLED
        db.commit()
        assert day_of(db, company, SOON).is_available is True

    def test_per_date_override_raises_capacity(self, db, company) -> None:
        for _ in range(av.DEFAULT_DAILY_CAPACITY):
            book(db, company, SOON)
        override(db, company, SOON, max_moves=av.DEFAULT_DAILY_CAPACITY + 1)
        assert day_of(db, company, SOON).is_available is True

    def test_zero_capacity_override_closes_the_date(self, db, company) -> None:
        override(db, company, SOON, max_moves=0)
        assert day_of(db, company, SOON).reason == av.REASON_FULL

    def test_block_beats_spare_capacity(self, db, company) -> None:
        override(db, company, SOON, is_blocked=True, note="Statutory holiday")
        assert day_of(db, company, SOON).reason == av.REASON_BLOCKED

    def test_another_company_booking_does_not_consume_our_capacity(self, db, company) -> None:
        other = Company(name="Bravo", slug="bravo-van-lines", settings={})
        db.add(other)
        db.commit()
        for _ in range(av.DEFAULT_DAILY_CAPACITY + 2):
            book(db, other, SOON)
        assert day_of(db, company, SOON).is_available is True


class TestCalendar:
    def test_returns_every_day_in_range_inclusive(self, db, company) -> None:
        days = av.availability_calendar(
            db, company, start=SOON, end=SOON + timedelta(days=4), today=TODAY
        )
        assert [d.date for d in days] == [SOON + timedelta(days=i) for i in range(5)]

    def test_rejects_an_inverted_range(self, db, company) -> None:
        with pytest.raises(ValidationError):
            av.availability_calendar(
                db, company, start=SOON, end=SOON - timedelta(days=1), today=TODAY
            )

    def test_rejects_an_oversized_range(self, db, company) -> None:
        with pytest.raises(ValidationError):
            av.availability_calendar(
                db,
                company,
                start=TODAY,
                end=TODAY + timedelta(days=av.MAX_CALENDAR_DAYS),
                today=TODAY,
            )

    def test_accepts_a_range_at_the_limit(self, db, company) -> None:
        days = av.availability_calendar(
            db,
            company,
            start=TODAY,
            end=TODAY + timedelta(days=av.MAX_CALENDAR_DAYS - 1),
            today=TODAY,
        )
        assert len(days) == av.MAX_CALENDAR_DAYS


class TestAssertAvailable:
    def test_passes_for_an_open_date(self, db, company) -> None:
        av.assert_available(db, company, SOON, today=TODAY)

    @pytest.mark.parametrize(
        ("setup", "fragment"),
        [
            (lambda db, c: override(db, c, SOON, is_blocked=True), "isn't available"),
            (
                lambda db, c: [book(db, c, SOON) for _ in range(av.DEFAULT_DAILY_CAPACITY)],
                "fully booked",
            ),
        ],
    )
    def test_raises_with_customer_safe_wording(self, db, company, setup, fragment) -> None:
        setup(db, company)
        with pytest.raises(ConflictError, match=fragment):
            av.assert_available(db, company, SOON, today=TODAY)

    def test_message_never_leaks_load_figures(self, db, company) -> None:
        for _ in range(av.DEFAULT_DAILY_CAPACITY):
            book(db, company, SOON)
        with pytest.raises(ConflictError) as exc:
            av.assert_available(db, company, SOON, today=TODAY)
        assert str(av.DEFAULT_DAILY_CAPACITY) not in str(exc.value)


class TestPublicApi:
    def test_returns_calendar_for_the_quote(self, client: TestClient, db, company) -> None:
        lead, request, quote = make_quote_chain(db, company)
        body = client.get(f"/api/v1/public/quotes/{quote.public_token}/availability").json()

        assert body["current_move_date"] == request.move_date.isoformat()
        assert body["days"]
        assert set(body["days"][0]) == {"date", "is_available", "reason"}

    def test_never_exposes_load_or_notes(self, client: TestClient, db, company) -> None:
        lead, request, quote = make_quote_chain(db, company)
        override(db, company, SOON, is_blocked=True, note="Owner is on vacation")
        raw = client.get(f"/api/v1/public/quotes/{quote.public_token}/availability").text
        assert "vacation" not in raw
        assert "booked_count" not in raw
        assert "capacity" not in raw

    def test_unknown_token_is_404(self, client: TestClient, company) -> None:
        assert client.get("/api/v1/public/quotes/nope/availability").status_code == 404

    def test_oversized_range_is_rejected(self, client: TestClient, db, company) -> None:
        lead, request, quote = make_quote_chain(db, company)
        resp = client.get(
            f"/api/v1/public/quotes/{quote.public_token}/availability",
            params={"from": "2026-09-10", "to": "2027-09-10"},
        )
        assert resp.status_code == 422

    def test_requires_no_authentication(self, client: TestClient, db, company) -> None:
        lead, request, quote = make_quote_chain(db, company)
        assert (
            client.get(f"/api/v1/public/quotes/{quote.public_token}/availability").status_code
            == 200
        )


class TestOwnerApi:
    URL = "/api/v1/availability"

    def test_requires_auth(self, client: TestClient, company) -> None:
        assert client.get(f"{self.URL}?from=2026-09-10&to=2026-09-20").status_code == 401

    def test_shows_load_and_capacity(self, client: TestClient, db, company, auth_headers) -> None:
        book(db, company, SOON)
        body = client.get(
            self.URL,
            params={"from": SOON.isoformat(), "to": SOON.isoformat()},
            headers=auth_headers,
        ).json()
        assert body[0]["booked_count"] == 1
        assert body[0]["capacity"] == av.DEFAULT_DAILY_CAPACITY

    def test_block_a_date_then_clear_it(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        blocked = client.put(
            f"{self.URL}/{SOON.isoformat()}",
            json={"is_blocked": True, "note": "Holiday"},
            headers=auth_headers,
        ).json()
        assert blocked["is_blocked"] is True
        assert blocked["is_available"] is False
        assert day_of(db, company, SOON).reason == av.REASON_BLOCKED

        assert (
            client.delete(f"{self.URL}/{SOON.isoformat()}", headers=auth_headers).status_code == 204
        )
        db.expire_all()
        assert day_of(db, company, SOON).is_available is True

    def test_upsert_is_idempotent(self, client: TestClient, db, company, auth_headers) -> None:
        for _ in range(3):
            client.put(
                f"{self.URL}/{SOON.isoformat()}", json={"max_moves": 5}, headers=auth_headers
            )
        rows = db.query(CompanyDateCapacity).filter_by(company_id=company.id, date=SOON).count()
        assert rows == 1

    def test_clearing_an_absent_date_succeeds(
        self, client: TestClient, company, auth_headers
    ) -> None:
        """Absent is the normal state; the caller's intent is satisfied either way."""
        assert (
            client.delete(f"{self.URL}/{SOON.isoformat()}", headers=auth_headers).status_code == 204
        )

    def test_staff_may_read_but_not_write(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        staff = User(
            id=uuid.uuid4(), company_id=company.id, email="s@acme.test", role=UserRole.STAFF
        )
        db.add(staff)
        db.commit()
        headers = {"Authorization": f"Bearer {mint_token(staff.id)}"}

        assert (
            client.get(
                self.URL, params={"from": SOON.isoformat(), "to": SOON.isoformat()}, headers=headers
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"{self.URL}/{SOON.isoformat()}", json={"is_blocked": True}, headers=headers
            ).status_code
            == 403
        )

    def test_calendar_is_tenant_scoped(self, client: TestClient, db, company, auth_headers) -> None:
        other = Company(name="Bravo", slug="bravo-van-lines", settings={})
        db.add(other)
        db.commit()
        for _ in range(3):
            book(db, other, SOON)

        body = client.get(
            self.URL,
            params={"from": SOON.isoformat(), "to": SOON.isoformat()},
            headers=auth_headers,
        ).json()
        assert body[0]["booked_count"] == 0
        assert body[0]["is_available"] is True

    def test_invalid_capacity_is_rejected(self, client: TestClient, company, auth_headers) -> None:
        resp = client.put(
            f"{self.URL}/{SOON.isoformat()}", json={"max_moves": -1}, headers=auth_headers
        )
        assert resp.status_code == 422
