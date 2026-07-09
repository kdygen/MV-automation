"""Integration tests for job completion, listing, CSV import, and accuracy stats."""

from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import (
    Booking,
    BookingStatus,
    Company,
    Job,
    JobSource,
    Lead,
    LeadStatus,
    User,
    UserRole,
)
from tests.conftest import mint_token
from tests.test_quotes_api import submit


def book_a_move(client: TestClient) -> str:
    """Submit + accept, returning the booking id."""
    token = submit(client)["quote"]["public_token"]
    resp = client.post(f"/api/v1/public/quotes/{token}/accept")
    assert resp.status_code == 200
    return resp.json()["booking_id"]


ACTUALS = {"actual_hours": 8.5, "actual_crew_size": 3, "actual_total_dollars": 1850.0}


class TestCompleteBooking:
    def test_complete_creates_job_with_denormalized_features(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        booking_id = book_a_move(client)
        resp = client.post(
            f"/api/v1/bookings/{booking_id}/complete", json=ACTUALS, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "platform"
        assert body["actual_hours"] == 8.5
        assert body["actual_total_cents"] == 185_000
        assert body["home_size"] == "2br"  # denormalized from the request
        assert body["quoted_hours"] is not None
        assert body["quoted_total_cents"] is not None

        booking = db.scalar(select(Booking))
        lead = db.scalar(select(Lead))
        job = db.scalar(select(Job))
        assert booking.status is BookingStatus.COMPLETED
        assert lead.status is LeadStatus.COMPLETED
        assert job.booking_id == booking.id
        assert job.source is JobSource.PLATFORM

    def test_double_complete_conflicts(self, client: TestClient, company, auth_headers) -> None:
        booking_id = book_a_move(client)
        client.post(f"/api/v1/bookings/{booking_id}/complete", json=ACTUALS, headers=auth_headers)
        resp = client.post(
            f"/api/v1/bookings/{booking_id}/complete", json=ACTUALS, headers=auth_headers
        )
        assert resp.status_code == 409

    def test_cross_tenant_complete_is_404(self, client: TestClient, db, company) -> None:
        booking_id = book_a_move(client)
        other = Company(name="Bravo", slug="bravo", settings={})
        db.add(other)
        db.flush()
        outsider = User(
            id=uuid.uuid4(), company_id=other.id, email="x@bravo.test", role=UserRole.OWNER
        )
        db.add(outsider)
        db.commit()
        resp = client.post(
            f"/api/v1/bookings/{booking_id}/complete",
            json=ACTUALS,
            headers={"Authorization": f"Bearer {mint_token(outsider.id)}"},
        )
        assert resp.status_code == 404

    def test_invalid_actuals_rejected(self, client: TestClient, company, auth_headers) -> None:
        booking_id = book_a_move(client)
        resp = client.post(
            f"/api/v1/bookings/{booking_id}/complete",
            json={"actual_hours": -2, "actual_crew_size": 3, "actual_total_dollars": 100},
            headers=auth_headers,
        )
        assert resp.status_code == 422


VALID_CSV = """\
move_date,home_size,packing_service,distance_miles,actual_hours,actual_crew_size,actual_total_dollars
2025-06-14,2br,none,12.5,6.0,3,1450
2025-07-02,3br,partial,8.0,9.5,3,2210.50
2025-08-19,studio,,3.2,3.0,2,520
"""

MIXED_CSV = """move_date,home_size,actual_hours,actual_crew_size,actual_total_dollars
2025-06-14,2br,6.0,3,1450
not-a-date,3br,9.5,3,2210
2025-08-19,mansion,3.0,2,520
2025-09-01,1br,4.0,zebra,700
2025-10-01,4br,11.0,4,3100
"""


class TestCsvImport:
    def test_valid_csv_imports_all_rows(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        resp = client.post(
            "/api/v1/jobs/import",
            files={"file": ("history.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"imported": 3, "errors": []}

        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 3
        assert all(j.source is JobSource.IMPORT for j in jobs)
        assert all(j.company_id == company.id for j in jobs)
        assert all(j.quoted_total_cents is None for j in jobs)
        assert {j.actual_total_cents for j in jobs} == {145_000, 221_050, 52_000}

    def test_bad_rows_reported_good_rows_imported(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        resp = client.post(
            "/api/v1/jobs/import",
            files={"file": ("history.csv", io.BytesIO(MIXED_CSV.encode()), "text/csv")},
            headers=auth_headers,
        )
        body = resp.json()
        assert body["imported"] == 2
        assert [e["row"] for e in body["errors"]] == [2, 3, 4]
        assert "not-a-date" in body["errors"][0]["message"]
        assert "mansion" in body["errors"][1]["message"]
        assert len(db.scalars(select(Job)).all()) == 2

    def test_missing_columns_rejected(self, client: TestClient, company, auth_headers) -> None:
        resp = client.post(
            "/api/v1/jobs/import",
            files={"file": ("bad.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert "missing required columns" in resp.json()["error"]["message"]

    def test_staff_cannot_import(self, client: TestClient, db, company) -> None:
        staff = User(
            id=uuid.uuid4(), company_id=company.id, email="s@acme.test", role=UserRole.STAFF
        )
        db.add(staff)
        db.commit()
        resp = client.post(
            "/api/v1/jobs/import",
            files={"file": ("h.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers={"Authorization": f"Bearer {mint_token(staff.id)}"},
        )
        assert resp.status_code == 403


class TestJobsAndAccuracy:
    def test_jobs_list_is_tenant_scoped(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        client.post(
            "/api/v1/jobs/import",
            files={"file": ("h.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_headers,
        )
        jobs = client.get("/api/v1/jobs", headers=auth_headers).json()
        assert len(jobs) == 3
        # Another tenant sees nothing.
        other = Company(name="Bravo", slug="bravo", settings={})
        db.add(other)
        db.flush()
        outsider = User(
            id=uuid.uuid4(), company_id=other.id, email="o@bravo.test", role=UserRole.OWNER
        )
        db.add(outsider)
        db.commit()
        assert (
            client.get(
                "/api/v1/jobs", headers={"Authorization": f"Bearer {mint_token(outsider.id)}"}
            ).json()
            == []
        )

    def test_accuracy_summary_math(self, client: TestClient, db, company, auth_headers) -> None:
        # Empty state: no stats yet.
        empty = client.get("/api/v1/jobs/accuracy", headers=auth_headers).json()
        assert empty == {
            "job_count": 0,
            "jobs_with_quote": 0,
            "total_mape_pct": None,
            "hours_mae": None,
        }

        # One platform job: quoted vs actual gives exact MAPE/MAE.
        booking_id = book_a_move(client)
        quote = client.get("/api/v1/quotes", headers=auth_headers).json()[0]
        client.post(
            f"/api/v1/bookings/{booking_id}/complete", json=ACTUALS, headers=auth_headers
        )
        job = db.scalar(select(Job))

        body = client.get("/api/v1/jobs/accuracy", headers=auth_headers).json()
        assert body["job_count"] == 1
        assert body["jobs_with_quote"] == 1
        expected_mape = (
            abs(job.actual_total_cents - job.quoted_total_cents) / job.quoted_total_cents * 100
        )
        assert body["total_mape_pct"] == round(expected_mape, 2)
        assert body["hours_mae"] == round(abs(8.5 - job.quoted_hours), 2)
        assert quote["status"] == "accepted"

        # Imported jobs raise job_count but not jobs_with_quote.
        client.post(
            "/api/v1/jobs/import",
            files={"file": ("h.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")},
            headers=auth_headers,
        )
        body = client.get("/api/v1/jobs/accuracy", headers=auth_headers).json()
        assert body["job_count"] == 4
        assert body["jobs_with_quote"] == 1
