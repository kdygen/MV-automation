"""Integration tests for the historical-move import API (Step 5D).

Written as attacks where it matters: a CSV carrying ``company_id``, a body carrying one,
another tenant's move id, a re-uploaded file, a malformed spreadsheet. The recurring
assertion is that **preview writes zero rows** and only confirmation writes.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models import Company, Job, JobImportBatch, JobSource, User, UserRole
from tests.conftest import mint_token

BASE = "/api/v1/history"
PAST = date.today() - timedelta(days=40)

MAPPING = {
    "move_date": "Move Date",
    "home_size": "Size",
    "actual_hours": "Hours",
    "actual_crew_size": "Men",
    "actual_total_cents": "Total",
}


def csv_bytes(rows: list[list[str]], headers: list[str] | None = None) -> bytes:
    head = headers or ["Move Date", "Size", "Hours", "Men", "Total"]
    lines = [",".join(head)] + [",".join(r) for r in rows]
    return "\n".join(lines).encode()


def good_row(**over) -> list[str]:
    values = {
        "Move Date": PAST.isoformat(),
        "Size": "2 bedroom",
        "Hours": "6.5",
        "Men": "3",
        "Total": "1450.00",
    }
    values.update(over)
    return list(values.values())


def upload(client, path: str, content: bytes, headers, *, request=None, name="history.csv"):
    files = {"file": (name, io.BytesIO(content), "text/csv")}
    data = {"request": json.dumps(request)} if request is not None else None
    return client.post(f"{BASE}{path}", files=files, data=data, headers=headers)


def count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture()
def rival(db):
    """A second tenant with its own history and its own owner."""
    company = Company(name="Bravo Van Lines", slug="bravo-van-lines", settings={})
    db.add(company)
    db.flush()
    job = Job(
        company_id=company.id,
        source=JobSource.IMPORT,
        move_date=PAST,
        home_size="3br",
        actual_hours=9.0,
        actual_crew_size=4,
        actual_total_cents=300_000,
        row_hash="rival-hash",
    )
    db.add(job)
    user = User(
        id=uuid.uuid4(), company_id=company.id, email="owner@bravo.test", role=UserRole.OWNER
    )
    db.add(user)
    db.commit()
    return {
        "company": company,
        "job_id": str(job.id),
        "headers": {"Authorization": f"Bearer {mint_token(user.id)}"},
    }


class TestInspect:
    def test_suggests_a_mapping_and_writes_nothing(self, client, db, company, auth_headers) -> None:
        resp = upload(client, "/import/inspect", csv_bytes([good_row()]), auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["row_count"] == 1
        assert body["suggested_mapping"]["actual_crew_size"] == "Men"
        assert count(db, Job) == 0 and count(db, JobImportBatch) == 0

    def test_lists_the_canonical_fields_for_manual_mapping(
        self, client, company, auth_headers
    ) -> None:
        body = upload(client, "/import/inspect", csv_bytes([good_row()]), auth_headers).json()
        names = {f["name"] for f in body["fields"]}
        assert {"move_date", "home_size", "actual_hours"} <= names
        assert "company_id" not in names
        required = {f["name"] for f in body["fields"] if f["required"]}
        assert required == {"move_date", "home_size"}

    def test_flags_ambiguous_dates(self, client, company, auth_headers) -> None:
        rows = [good_row(**{"Move Date": "03/04/2024"}), good_row(**{"Move Date": "05/06/2024"})]
        body = upload(client, "/import/inspect", csv_bytes(rows), auth_headers).json()
        assert body["ambiguity"]["date_ambiguous"] is True
        assert body["ambiguity"]["needs_input"] is True

    def test_unsupported_extension_is_rejected(self, client, company, auth_headers) -> None:
        resp = upload(client, "/import/inspect", b"whatever", auth_headers, name="history.pdf")
        assert resp.status_code == 422

    def test_empty_file_is_rejected(self, client, company, auth_headers) -> None:
        assert upload(client, "/import/inspect", b"", auth_headers).status_code == 422

    def test_headers_without_rows_are_rejected(self, client, company, auth_headers) -> None:
        resp = upload(client, "/import/inspect", csv_bytes([]), auth_headers)
        assert resp.status_code == 422
        assert "no rows" in resp.json()["error"]["message"]


class TestPreviewWritesNothing:
    def test_preview_reports_counts_without_writing(
        self, client, db, company, auth_headers
    ) -> None:
        rows = [good_row(), good_row(**{"Size": ""}), good_row()]
        resp = upload(
            client, "/import/preview", csv_bytes(rows), auth_headers, request={"mapping": MAPPING}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        assert body["importable"] == 1  # row 3 duplicates row 1
        assert body["rejected"] == 1
        assert body["duplicates"] == 1
        assert count(db, Job) == 0 and count(db, JobImportBatch) == 0

    def test_repeated_previews_still_write_nothing(self, client, db, company, auth_headers) -> None:
        for _ in range(4):
            upload(
                client,
                "/import/preview",
                csv_bytes([good_row()]),
                auth_headers,
                request={"mapping": MAPPING},
            )
        assert count(db, Job) == 0

    def test_problem_rows_are_listed_first(self, client, company, auth_headers) -> None:
        rows = [good_row(**{"Hours": f"{n}"}) for n in range(5, 12)] + [good_row(**{"Size": ""})]
        body = upload(
            client, "/import/preview", csv_bytes(rows), auth_headers, request={"mapping": MAPPING}
        ).json()
        assert body["rows"][0]["status"] == "error"

    def test_ambiguous_dates_block_the_preview(self, client, company, auth_headers) -> None:
        rows = [good_row(**{"Move Date": "03/04/2024"}), good_row(**{"Move Date": "05/06/2024"})]
        resp = upload(
            client, "/import/preview", csv_bytes(rows), auth_headers, request={"mapping": MAPPING}
        )
        assert resp.status_code == 422
        assert "day/month or month/day" in resp.json()["error"]["message"]

    def test_an_explicit_date_order_unblocks_it(self, client, company, auth_headers) -> None:
        rows = [good_row(**{"Move Date": "03/04/2024"})]
        resp = upload(
            client,
            "/import/preview",
            csv_bytes(rows),
            auth_headers,
            request={"mapping": MAPPING, "date_order": "dmy"},
        )
        assert resp.status_code == 200
        assert resp.json()["rows"][0]["preview"]["move_date"] == "2024-04-03"

    def test_ambiguous_money_blocks_the_preview(self, client, company, auth_headers) -> None:
        rows = [good_row(**{"Total": "1.450"}), good_row(**{"Total": "2.500", "Hours": "7"})]
        resp = upload(
            client, "/import/preview", csv_bytes(rows), auth_headers, request={"mapping": MAPPING}
        )
        assert resp.status_code == 422
        assert "decimal style" in resp.json()["error"]["message"]

    def test_missing_required_mapping_is_rejected(self, client, company, auth_headers) -> None:
        resp = upload(
            client,
            "/import/preview",
            csv_bytes([good_row()]),
            auth_headers,
            request={"mapping": {"actual_hours": "Hours"}},
        )
        assert resp.status_code == 422
        assert "must be mapped" in resp.json()["error"]["message"]

    def test_mapping_with_no_outcome_is_rejected(self, client, company, auth_headers) -> None:
        resp = upload(
            client,
            "/import/preview",
            csv_bytes([good_row()]),
            auth_headers,
            request={"mapping": {"move_date": "Move Date", "home_size": "Size"}},
        )
        assert resp.status_code == 422
        assert "no outcome" in resp.json()["error"]["message"]

    def test_mapping_a_column_the_file_lacks_is_rejected(
        self, client, company, auth_headers
    ) -> None:
        resp = upload(
            client,
            "/import/preview",
            csv_bytes([good_row()]),
            auth_headers,
            request={"mapping": {**MAPPING, "distance_miles": "Nope"}},
        )
        assert resp.status_code == 422
        assert "no column" in resp.json()["error"]["message"]

    def test_malformed_mapping_json_is_rejected(self, client, company, auth_headers) -> None:
        files = {"file": ("h.csv", io.BytesIO(csv_bytes([good_row()])), "text/csv")}
        resp = client.post(
            f"{BASE}/import/preview",
            files=files,
            data={"request": "{not json"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestTenantInjection:
    def test_a_company_id_column_in_the_csv_cannot_be_mapped(
        self, client, db, company, auth_headers, rival
    ) -> None:
        """The registry has no company_id field, so the column has nowhere to land."""
        headers = ["Move Date", "Size", "Hours", "Men", "Total", "company_id"]
        rows = [good_row() + [str(rival["company"].id)]]
        resp = upload(
            client,
            "/import/confirm",
            csv_bytes(rows, headers),
            auth_headers,
            request={"mapping": {**MAPPING, "company_id": "company_id"}},
        )
        assert resp.status_code == 422
        assert "Unknown field" in resp.json()["error"]["message"]
        assert count(db, Job) == 1  # only the rival's own pre-existing row

    def test_a_company_id_column_is_ignored_when_unmapped(
        self, client, db, company, auth_headers, rival
    ) -> None:
        headers = ["Move Date", "Size", "Hours", "Men", "Total", "company_id"]
        rows = [good_row() + [str(rival["company"].id)]]
        resp = upload(
            client,
            "/import/confirm",
            csv_bytes(rows, headers),
            auth_headers,
            request={"mapping": MAPPING},
        )
        assert resp.status_code == 201
        imported = db.scalar(select(Job).where(Job.company_id == company.id))
        assert imported.company_id == company.id
        assert (
            db.scalar(
                select(func.count()).select_from(Job).where(Job.company_id == rival["company"].id)
            )
            == 1
        )

    def test_company_id_in_the_request_body_is_refused(self, client, company, auth_headers) -> None:
        resp = upload(
            client,
            "/import/confirm",
            csv_bytes([good_row()]),
            auth_headers,
            request={"mapping": MAPPING, "company_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 422

    def test_pii_columns_never_enter(self, client, db, company, auth_headers) -> None:
        headers = ["Move Date", "Size", "Hours", "Men", "Total", "Customer", "Email", "Card"]
        rows = [good_row() + ["Jane Doe", "jane@example.com", "4242424242424242"]]
        resp = upload(
            client,
            "/import/confirm",
            csv_bytes(rows, headers),
            auth_headers,
            request={"mapping": MAPPING},
        )
        assert resp.status_code == 201
        job = db.scalar(select(Job).where(Job.company_id == company.id))
        rendered = str({c.name: getattr(job, c.name) for c in Job.__table__.columns})
        for leak in ("Jane", "jane@example.com", "4242"):
            assert leak not in rendered


class TestConfirm:
    def test_confirmation_writes_the_expected_rows(self, client, db, company, auth_headers) -> None:
        rows = [good_row(), good_row(**{"Hours": "8", "Total": "1800.00"})]
        resp = upload(
            client, "/import/confirm", csv_bytes(rows), auth_headers, request={"mapping": MAPPING}
        )
        assert resp.status_code == 201, resp.text
        batch = resp.json()["batch"]
        assert batch["row_count_imported"] == 2
        assert count(db, Job) == 2

        job = db.scalars(select(Job).order_by(Job.actual_hours)).first()
        assert job.company_id == company.id
        assert job.source is JobSource.IMPORT
        assert job.import_batch_id is not None
        assert job.row_hash is not None
        assert job.actual_total_cents == 145_000

    def test_the_batch_records_provenance(self, client, db, company, auth_headers) -> None:
        upload(
            client,
            "/import/confirm",
            csv_bytes([good_row()]),
            auth_headers,
            request={"mapping": MAPPING},
            name="2023-history.csv",
        )
        batch = db.scalar(select(JobImportBatch))
        assert batch.filename == "2023-history.csv"
        assert batch.file_format.value == "csv"
        assert batch.column_mapping["actual_hours"] == "Hours"
        assert batch.parse_options["date_order"]
        assert batch.created_by_user_id is not None

    def test_rejected_rows_do_not_stop_good_ones(self, client, db, company, auth_headers) -> None:
        rows = [good_row(), good_row(**{"Size": "", "Hours": "9"}), good_row(**{"Hours": "7"})]
        body = upload(
            client, "/import/confirm", csv_bytes(rows), auth_headers, request={"mapping": MAPPING}
        ).json()
        assert body["batch"]["row_count_imported"] == 2
        assert body["batch"]["row_count_rejected"] == 1
        assert len(body["rejected_rows"]) == 1
        assert count(db, Job) == 2

    def test_an_all_bad_file_imports_nothing(self, client, db, company, auth_headers) -> None:
        rows = [good_row(**{"Size": ""}), good_row(**{"Size": "castle"})]
        resp = upload(
            client, "/import/confirm", csv_bytes(rows), auth_headers, request={"mapping": MAPPING}
        )
        assert resp.status_code == 409
        assert count(db, Job) == 0 and count(db, JobImportBatch) == 0

    def test_optional_fields_import_when_mapped(self, client, db, company, auth_headers) -> None:
        headers = ["Move Date", "Size", "Hours", "Men", "Total", "From Zip", "Stairs From", "Lift"]
        rows = [good_row() + ["62701", "3", "no"]]
        mapping = {
            **MAPPING,
            "origin_zip": "From Zip",
            "origin_stairs_flights": "Stairs From",
            "origin_has_elevator": "Lift",
        }
        resp = upload(
            client,
            "/import/confirm",
            csv_bytes(rows, headers),
            auth_headers,
            request={"mapping": mapping},
        )
        assert resp.status_code == 201
        job = db.scalar(select(Job))
        assert job.origin_zip == "62701"
        assert job.origin_stairs_flights == 3
        assert job.origin_has_elevator is False

    def test_street_addresses_only_arrive_when_deliberately_mapped(
        self, client, db, company, auth_headers
    ) -> None:
        headers = ["Move Date", "Size", "Hours", "Men", "Total", "Addr", "From Zip"]
        rows = [good_row() + ["123 Main St", "62701"]]
        # First without mapping the address column at all.
        upload(
            client,
            "/import/confirm",
            csv_bytes(rows, headers),
            auth_headers,
            request={"mapping": {**MAPPING, "origin_zip": "From Zip"}},
        )
        job = db.scalar(select(Job))
        assert job.origin_line1 is None
        assert job.origin_building_key is None

    def test_mapping_an_address_derives_a_building_key(
        self, client, db, company, auth_headers
    ) -> None:
        headers = ["Move Date", "Size", "Hours", "Men", "Total", "Addr", "From Zip"]
        rows = [good_row() + ["123 Main St", "62701"]]
        upload(
            client,
            "/import/confirm",
            csv_bytes(rows, headers),
            auth_headers,
            request={"mapping": {**MAPPING, "origin_line1": "Addr", "origin_zip": "From Zip"}},
        )
        job = db.scalar(select(Job))
        assert job.origin_line1 == "123 Main St"
        assert job.origin_building_key == "123 main st|62701"


class TestIdempotency:
    def test_uploading_the_same_file_twice_imports_once(
        self, client, db, company, auth_headers
    ) -> None:
        content = csv_bytes([good_row(), good_row(**{"Hours": "8"})])
        first = upload(
            client, "/import/confirm", content, auth_headers, request={"mapping": MAPPING}
        ).json()
        assert first["batch"]["row_count_imported"] == 2

        second = upload(
            client, "/import/confirm", content, auth_headers, request={"mapping": MAPPING}
        )
        assert second.status_code == 409
        assert count(db, Job) == 2

    def test_a_second_preview_reports_everything_as_duplicate(
        self, client, db, company, auth_headers
    ) -> None:
        content = csv_bytes([good_row()])
        upload(client, "/import/confirm", content, auth_headers, request={"mapping": MAPPING})
        body = upload(
            client, "/import/preview", content, auth_headers, request={"mapping": MAPPING}
        ).json()
        assert body["duplicates"] == 1
        assert body["importable"] == 0

    def test_a_grown_export_imports_only_the_new_rows(
        self, client, db, company, auth_headers
    ) -> None:
        """Re-exporting next month must add the new moves, not re-add the old ones."""
        upload(
            client,
            "/import/confirm",
            csv_bytes([good_row()]),
            auth_headers,
            request={"mapping": MAPPING},
        )
        grown = csv_bytes([good_row(), good_row(**{"Hours": "9", "Total": "2000.00"})])
        body = upload(
            client, "/import/confirm", grown, auth_headers, request={"mapping": MAPPING}
        ).json()
        assert body["batch"]["row_count_imported"] == 1
        assert body["batch"]["row_count_skipped"] == 1
        assert count(db, Job) == 2

    def test_a_job_number_makes_identical_rows_distinct(
        self, client, db, company, auth_headers
    ) -> None:
        headers = ["Move Date", "Size", "Hours", "Men", "Total", "Job #"]
        rows = [good_row() + ["A-1"], good_row() + ["A-2"]]
        body = upload(
            client,
            "/import/confirm",
            csv_bytes(rows, headers),
            auth_headers,
            request={"mapping": {**MAPPING, "external_ref": "Job #"}},
        ).json()
        assert body["batch"]["row_count_imported"] == 2
        assert count(db, Job) == 2


class TestRevert:
    def test_revert_removes_exactly_the_batch_rows(self, client, db, company, auth_headers) -> None:
        upload(
            client,
            "/import/confirm",
            csv_bytes([good_row()]),
            auth_headers,
            request={"mapping": MAPPING},
        )
        second = upload(
            client,
            "/import/confirm",
            csv_bytes([good_row(**{"Hours": "9"})]),
            auth_headers,
            request={"mapping": MAPPING},
        ).json()
        assert count(db, Job) == 2

        resp = client.post(f"{BASE}/imports/{second['batch']['id']}/revert", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["reverted_at"] is not None
        assert count(db, Job) == 1

    def test_reverting_twice_is_refused(self, client, db, company, auth_headers) -> None:
        batch = upload(
            client,
            "/import/confirm",
            csv_bytes([good_row()]),
            auth_headers,
            request={"mapping": MAPPING},
        ).json()["batch"]
        client.post(f"{BASE}/imports/{batch['id']}/revert", headers=auth_headers)
        again = client.post(f"{BASE}/imports/{batch['id']}/revert", headers=auth_headers)
        assert again.status_code == 409

    def test_a_reverted_import_can_be_uploaded_again(
        self, client, db, company, auth_headers
    ) -> None:
        """Reverting must actually free the rows, or a bad mapping is unrecoverable."""
        content = csv_bytes([good_row()])
        batch = upload(
            client, "/import/confirm", content, auth_headers, request={"mapping": MAPPING}
        ).json()["batch"]
        client.post(f"{BASE}/imports/{batch['id']}/revert", headers=auth_headers)

        resp = upload(
            client, "/import/confirm", content, auth_headers, request={"mapping": MAPPING}
        )
        assert resp.status_code == 201
        assert count(db, Job) == 1

    def test_cross_tenant_revert_is_404(self, client, db, company, auth_headers, rival) -> None:
        batch = JobImportBatch(
            company_id=rival["company"].id,
            filename="theirs.csv",
            file_format="csv",
            column_mapping={},
            parse_options={},
        )
        db.add(batch)
        db.commit()
        resp = client.post(f"{BASE}/imports/{batch.id}/revert", headers=auth_headers)
        assert resp.status_code == 404
        db.refresh(batch)
        assert batch.reverted_at is None


class TestManualEntry:
    PAYLOAD = {
        "move_date": PAST.isoformat(),
        "home_size": "3br",
        "actual_hours": 8.0,
        "actual_crew_size": 4,
        "actual_total_dollars": 2100.0,
        "origin_zip": "62701",
        "problem_notes": "Freight elevator unavailable until 1 PM.",
    }

    def test_creates_a_move(self, client, db, company, auth_headers) -> None:
        resp = client.post(BASE, json=self.PAYLOAD, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["actual_total_cents"] == 210_000
        assert body["problem_notes"].startswith("Freight elevator")
        job = db.scalar(select(Job))
        assert job.source is JobSource.IMPORT
        assert job.import_batch_id is None
        assert job.row_hash is not None

    def test_uses_the_same_outcome_rule_as_the_importer(
        self, client, db, company, auth_headers
    ) -> None:
        payload = dict(self.PAYLOAD)
        payload.pop("actual_hours")
        payload.pop("actual_total_dollars")
        resp = client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 422
        assert "at least actual hours" in resp.json()["error"]["message"]
        assert count(db, Job) == 0

    def test_future_dates_are_refused(self, client, company, auth_headers) -> None:
        payload = {**self.PAYLOAD, "move_date": (date.today() + timedelta(days=3)).isoformat()}
        assert client.post(BASE, json=payload, headers=auth_headers).status_code == 422

    def test_a_duplicate_manual_entry_is_refused(self, client, db, company, auth_headers) -> None:
        client.post(BASE, json=self.PAYLOAD, headers=auth_headers)
        resp = client.post(BASE, json=self.PAYLOAD, headers=auth_headers)
        assert resp.status_code == 409
        assert count(db, Job) == 1

    def test_unknown_fields_are_refused(self, client, company, auth_headers) -> None:
        for extra in ({"company_id": str(uuid.uuid4())}, {"customer_name": "Jane"}):
            resp = client.post(BASE, json={**self.PAYLOAD, **extra}, headers=auth_headers)
            assert resp.status_code == 422, extra

    def test_impossible_values_are_refused(self, client, company, auth_headers) -> None:
        for bad in ({"actual_crew_size": 0}, {"actual_hours": -2}, {"actual_hours": 900}):
            resp = client.post(BASE, json={**self.PAYLOAD, **bad}, headers=auth_headers)
            assert resp.status_code == 422, bad

    def test_update_and_delete(self, client, db, company, auth_headers) -> None:
        move_id = client.post(BASE, json=self.PAYLOAD, headers=auth_headers).json()["id"]
        patched = client.patch(
            f"{BASE}/{move_id}", json={"actual_hours": 9.5}, headers=auth_headers
        )
        assert patched.status_code == 200
        assert patched.json()["actual_hours"] == 9.5
        assert client.delete(f"{BASE}/{move_id}", headers=auth_headers).status_code == 204
        assert count(db, Job) == 0


class TestViews:
    def test_list_and_detail_are_tenant_scoped(
        self, client, db, company, auth_headers, rival
    ) -> None:
        client.post(BASE, json=TestManualEntry.PAYLOAD, headers=auth_headers)
        mine = client.get(BASE, headers=auth_headers).json()
        assert len(mine) == 1
        assert client.get(f"{BASE}/{rival['job_id']}", headers=auth_headers).status_code == 404

    def test_list_excludes_street_addresses(self, client, db, company, auth_headers) -> None:
        client.post(
            BASE,
            json={**TestManualEntry.PAYLOAD, "origin_line1": "123 Main St"},
            headers=auth_headers,
        )
        raw = client.get(BASE, headers=auth_headers).text
        assert "123 Main St" not in raw

    def test_detail_shows_the_address_to_the_owner(self, client, db, company, auth_headers) -> None:
        move_id = client.post(
            BASE,
            json={**TestManualEntry.PAYLOAD, "origin_line1": "123 Main St"},
            headers=auth_headers,
        ).json()["id"]
        assert client.get(f"{BASE}/{move_id}", headers=auth_headers).json()["origin_line1"] == (
            "123 Main St"
        )

    def test_summary_reports_medians_and_provenance_counts(
        self, client, db, company, auth_headers
    ) -> None:
        rows = [good_row(**{"Hours": "6"}), good_row(**{"Hours": "8", "Total": "1600.00"})]
        upload(
            client, "/import/confirm", csv_bytes(rows), auth_headers, request={"mapping": MAPPING}
        )
        body = client.get(f"{BASE}/summary", headers=auth_headers).json()
        assert body["total_moves"] == 2
        assert body["imported_moves"] == 2
        assert body["platform_moves"] == 0
        assert body["median_actual_hours"] == 7.0

    def test_summary_is_tenant_scoped(self, client, db, company, auth_headers, rival) -> None:
        body = client.get(f"{BASE}/summary", headers=auth_headers).json()
        assert body["total_moves"] == 0

    def test_source_filter(self, client, db, company, auth_headers) -> None:
        client.post(BASE, json=TestManualEntry.PAYLOAD, headers=auth_headers)
        assert len(client.get(f"{BASE}?source=import", headers=auth_headers).json()) == 1
        assert client.get(f"{BASE}?source=platform", headers=auth_headers).json() == []


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [("GET", ""), ("GET", "/summary"), ("GET", "/imports"), ("POST", "")],
    )
    def test_unauthenticated_is_401(self, client, company, method, path) -> None:
        assert client.request(method, f"{BASE}{path}", json={}).status_code == 401

    def test_staff_may_read_but_not_write(self, client, db, company, auth_headers) -> None:
        staff = User(
            id=uuid.uuid4(), company_id=company.id, email="s@acme.test", role=UserRole.STAFF
        )
        db.add(staff)
        db.commit()
        headers = {"Authorization": f"Bearer {mint_token(staff.id)}"}

        assert client.get(BASE, headers=headers).status_code == 200
        assert client.post(BASE, json=TestManualEntry.PAYLOAD, headers=headers).status_code == 403
        assert (
            upload(
                client,
                "/import/confirm",
                csv_bytes([good_row()]),
                headers,
                request={"mapping": MAPPING},
            ).status_code
            == 403
        )
