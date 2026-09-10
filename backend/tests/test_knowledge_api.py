"""Integration tests for the company knowledge dashboard API (Step 3B).

Tenant isolation is tested as an attack: a second company's entry id is fetched and
then used directly against this company's session, for read, update, and delete.

The last class closes the loop with Step 3A — an entry created through this API must
be immediately usable by the sales agent, because both sides address the same table.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.agent import ToolExecutor
from app.models import Company, CompanyKnowledge, User, UserRole
from app.services import agent as agent_service
from app.services.knowledge import KNOWLEDGE_CATEGORIES, STARTER_TOPICS
from tests.conftest import mint_token
from tests.test_conversation_models import make_quote_chain

URL = "/api/v1/knowledge"

ENTRY = {
    "category": "insurance",
    "title": "Certificate of Insurance",
    "content": "We provide a COI at no charge with three business days' notice.",
    "keywords": "COI, certificate of insurance",
}


def create(client: TestClient, headers: dict[str, str], **overrides) -> dict:
    """POST one entry and return the created body, asserting success."""
    payload = {**ENTRY, **overrides}
    response = client.post(URL, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def make_staff(db, company: Company, role: UserRole, email: str) -> dict[str, str]:
    """Provision a user in ``company`` with ``role`` and return their auth headers."""
    user = User(id=uuid.uuid4(), company_id=company.id, email=email, role=role)
    db.add(user)
    db.commit()
    return {"Authorization": f"Bearer {mint_token(user.id)}"}


@pytest.fixture()
def rival(db):
    """A second tenant with one entry, plus headers for its owner."""
    company = Company(name="Bravo Van Lines", slug="bravo-van-lines", settings={})
    db.add(company)
    db.flush()
    entry = CompanyKnowledge(
        company_id=company.id,
        category="policy",
        title="Bravo cancellation policy",
        content="Bravo charges a $200 cancellation fee.",
        is_active=True,
    )
    db.add(entry)
    db.flush()
    headers = make_staff(db, company, UserRole.OWNER, "owner@bravo.test")
    return {"company": company, "entry_id": str(entry.id), "headers": headers}


class TestList:
    def test_starts_empty(self, client: TestClient, company, auth_headers) -> None:
        assert client.get(URL, headers=auth_headers).json() == []

    def test_lists_own_entries_with_expected_fields(
        self, client: TestClient, company, auth_headers
    ) -> None:
        create(client, auth_headers)
        body = client.get(URL, headers=auth_headers).json()

        assert len(body) == 1
        assert set(body[0]) == {
            "id",
            "category",
            "title",
            "content",
            "keywords",
            "is_active",
            "updated_at",
        }
        assert body[0]["title"] == ENTRY["title"]
        assert body[0]["is_active"] is True
        # company_id is never returned, in either direction.
        assert "company_id" not in body[0]

    def test_includes_inactive_entries(self, client: TestClient, company, auth_headers) -> None:
        """The dashboard is where an owner re-activates something; hiding it strands it."""
        entry = create(client, auth_headers, is_active=False)
        body = client.get(URL, headers=auth_headers).json()
        assert [e["id"] for e in body] == [entry["id"]]
        assert body[0]["is_active"] is False

    def test_ordered_by_category_then_title(
        self, client: TestClient, company, auth_headers
    ) -> None:
        create(client, auth_headers, category="policy", title="Zebra policy")
        create(client, auth_headers, category="policy", title="Apple policy")
        create(client, auth_headers, category="access", title="Stairs")

        body = client.get(URL, headers=auth_headers).json()
        assert [(e["category"], e["title"]) for e in body] == [
            ("access", "Stairs"),
            ("policy", "Apple policy"),
            ("policy", "Zebra policy"),
        ]


class TestCreate:
    def test_creates_and_persists(self, client: TestClient, db, company, auth_headers) -> None:
        created = create(client, auth_headers)
        row = db.get(CompanyKnowledge, uuid.UUID(created["id"]))
        assert row is not None
        assert row.company_id == company.id
        assert row.content == ENTRY["content"]

    def test_defaults_to_active(self, client: TestClient, company, auth_headers) -> None:
        assert create(client, auth_headers)["is_active"] is True

    def test_trims_whitespace(self, client: TestClient, company, auth_headers) -> None:
        created = create(
            client,
            auth_headers,
            category="  policy  ",
            title="  Cancellation policy  ",
            content="  Cancel free up to 48 hours before.  ",
            keywords="  cancel, refund  ",
        )
        assert created["category"] == "policy"
        assert created["title"] == "Cancellation policy"
        assert created["content"] == "Cancel free up to 48 hours before."
        assert created["keywords"] == "cancel, refund"

    def test_keywords_are_optional_and_stored_as_null(
        self, client: TestClient, company, auth_headers
    ) -> None:
        payload = {k: v for k, v in ENTRY.items() if k != "keywords"}
        assert client.post(URL, json=payload, headers=auth_headers).json()["keywords"] is None

    def test_blank_keywords_become_null_not_empty_string(
        self, client: TestClient, company, auth_headers
    ) -> None:
        """One representation for "no keywords", so search never sees an empty string."""
        assert create(client, auth_headers, keywords="   ")["keywords"] is None

    @pytest.mark.parametrize("field", ["category", "title", "content"])
    def test_required_fields_cannot_be_blank(
        self, client: TestClient, company, auth_headers, field: str
    ) -> None:
        for blank in ("", "   ", "\n\t"):
            response = client.post(URL, json={**ENTRY, field: blank}, headers=auth_headers)
            assert response.status_code == 422, (field, blank)
            assert response.json()["error"]["code"] == "validation_error"

    @pytest.mark.parametrize("field", ["category", "title", "content"])
    def test_required_fields_cannot_be_missing(
        self, client: TestClient, company, auth_headers, field: str
    ) -> None:
        payload = {k: v for k, v in ENTRY.items() if k != field}
        assert client.post(URL, json=payload, headers=auth_headers).status_code == 422

    @pytest.mark.parametrize(
        ("field", "length"),
        [("category", 51), ("title", 201), ("content", 4001), ("keywords", 501)],
    )
    def test_length_limits(
        self, client: TestClient, company, auth_headers, field: str, length: int
    ) -> None:
        payload = {**ENTRY, field: "x" * length}
        assert client.post(URL, json=payload, headers=auth_headers).status_code == 422

    def test_company_id_in_the_body_is_refused(
        self, client: TestClient, db, company, auth_headers, rival
    ) -> None:
        """Identity is server-owned: a client cannot even name a company."""
        payload = {**ENTRY, "company_id": str(rival["company"].id)}
        response = client.post(URL, json=payload, headers=auth_headers)
        assert response.status_code == 422
        assert db.scalar(
            CompanyKnowledge.__table__.select().where(
                CompanyKnowledge.company_id == rival["company"].id,
                CompanyKnowledge.title == ENTRY["title"],
            )
        ) is None

    def test_unknown_fields_are_refused(self, client: TestClient, company, auth_headers) -> None:
        payload = {**ENTRY, "id": str(uuid.uuid4())}
        assert client.post(URL, json=payload, headers=auth_headers).status_code == 422


class TestDuplicateTitle:
    def test_duplicate_title_is_conflict(self, client: TestClient, company, auth_headers) -> None:
        create(client, auth_headers)
        response = client.post(URL, json=ENTRY, headers=auth_headers)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_duplicate_detection_uses_the_trimmed_title(
        self, client: TestClient, company, auth_headers
    ) -> None:
        create(client, auth_headers)
        response = client.post(
            URL, json={**ENTRY, "title": f"  {ENTRY['title']}  "}, headers=auth_headers
        )
        assert response.status_code == 409

    def test_two_tenants_may_use_the_same_title(
        self, client: TestClient, company, auth_headers, rival
    ) -> None:
        create(client, auth_headers, title="Bravo cancellation policy")
        assert client.get(URL, headers=rival["headers"]).status_code == 200


class TestUpdate:
    def test_partial_update_leaves_other_fields_alone(
        self, client: TestClient, company, auth_headers
    ) -> None:
        entry = create(client, auth_headers)
        response = client.patch(
            f"{URL}/{entry['id']}", json={"content": "Updated answer."}, headers=auth_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["content"] == "Updated answer."
        assert body["title"] == ENTRY["title"]
        assert body["keywords"] == ENTRY["keywords"]

    def test_empty_patch_is_a_no_op(self, client: TestClient, company, auth_headers) -> None:
        entry = create(client, auth_headers)
        body = client.patch(f"{URL}/{entry['id']}", json={}, headers=auth_headers).json()
        assert body["title"] == entry["title"]
        assert body["content"] == entry["content"]

    def test_keywords_can_be_cleared_with_null(
        self, client: TestClient, company, auth_headers
    ) -> None:
        entry = create(client, auth_headers)
        body = client.patch(
            f"{URL}/{entry['id']}", json={"keywords": None}, headers=auth_headers
        ).json()
        assert body["keywords"] is None

    def test_blank_content_is_rejected(self, client: TestClient, company, auth_headers) -> None:
        entry = create(client, auth_headers)
        response = client.patch(
            f"{URL}/{entry['id']}", json={"content": "   "}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_renaming_onto_another_title_is_conflict(
        self, client: TestClient, company, auth_headers
    ) -> None:
        create(client, auth_headers)
        other = create(client, auth_headers, title="Stairs", category="access")
        response = client.patch(
            f"{URL}/{other['id']}", json={"title": ENTRY["title"]}, headers=auth_headers
        )
        assert response.status_code == 409

    def test_keeping_its_own_title_is_allowed(
        self, client: TestClient, company, auth_headers
    ) -> None:
        """The uniqueness check must exclude the row being edited."""
        entry = create(client, auth_headers)
        response = client.patch(
            f"{URL}/{entry['id']}",
            json={"title": ENTRY["title"], "content": "Reworded."},
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_unknown_entry_is_404(self, client: TestClient, company, auth_headers) -> None:
        response = client.patch(
            f"{URL}/{uuid.uuid4()}", json={"content": "x"}, headers=auth_headers
        )
        assert response.status_code == 404


class TestActivation:
    def test_deactivate_then_reactivate(self, client: TestClient, company, auth_headers) -> None:
        entry = create(client, auth_headers)

        off = client.patch(
            f"{URL}/{entry['id']}", json={"is_active": False}, headers=auth_headers
        ).json()
        assert off["is_active"] is False

        on = client.patch(
            f"{URL}/{entry['id']}", json={"is_active": True}, headers=auth_headers
        ).json()
        assert on["is_active"] is True


class TestDelete:
    def test_delete_removes_the_entry(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        entry = create(client, auth_headers)
        assert client.delete(f"{URL}/{entry['id']}", headers=auth_headers).status_code == 204
        assert db.get(CompanyKnowledge, uuid.UUID(entry["id"])) is None
        assert client.get(URL, headers=auth_headers).json() == []

    def test_deleting_twice_is_404(self, client: TestClient, company, auth_headers) -> None:
        entry = create(client, auth_headers)
        client.delete(f"{URL}/{entry['id']}", headers=auth_headers)
        assert client.delete(f"{URL}/{entry['id']}", headers=auth_headers).status_code == 404

    def test_unknown_entry_is_404(self, client: TestClient, company, auth_headers) -> None:
        assert client.delete(f"{URL}/{uuid.uuid4()}", headers=auth_headers).status_code == 404


class TestTenantIsolation:
    """Company A must not read, edit, or delete company B's entries by guessing ids."""

    def test_list_excludes_other_tenants(
        self, client: TestClient, company, auth_headers, rival
    ) -> None:
        create(client, auth_headers)
        titles = {e["title"] for e in client.get(URL, headers=auth_headers).json()}
        assert titles == {ENTRY["title"]}
        assert "Bravo cancellation policy" not in titles

    def test_cross_tenant_update_is_404(
        self, client: TestClient, company, auth_headers, rival
    ) -> None:
        response = client.patch(
            f"{URL}/{rival['entry_id']}", json={"content": "hacked"}, headers=auth_headers
        )
        assert response.status_code == 404

    def test_cross_tenant_update_does_not_modify_the_row(
        self, client: TestClient, db, company, auth_headers, rival
    ) -> None:
        client.patch(
            f"{URL}/{rival['entry_id']}", json={"content": "hacked"}, headers=auth_headers
        )
        row = db.get(CompanyKnowledge, uuid.UUID(rival["entry_id"]))
        assert row is not None
        assert row.content == "Bravo charges a $200 cancellation fee."

    def test_cross_tenant_deactivate_is_404(
        self, client: TestClient, db, company, auth_headers, rival
    ) -> None:
        """Silencing a competitor's agent must be impossible."""
        response = client.patch(
            f"{URL}/{rival['entry_id']}", json={"is_active": False}, headers=auth_headers
        )
        assert response.status_code == 404
        assert db.get(CompanyKnowledge, uuid.UUID(rival["entry_id"])).is_active is True

    def test_cross_tenant_delete_is_404_and_leaves_the_row(
        self, client: TestClient, db, company, auth_headers, rival
    ) -> None:
        response = client.delete(f"{URL}/{rival['entry_id']}", headers=auth_headers)
        assert response.status_code == 404
        assert db.get(CompanyKnowledge, uuid.UUID(rival["entry_id"])) is not None

    def test_404_is_indistinguishable_from_a_nonexistent_id(
        self, client: TestClient, company, auth_headers, rival
    ) -> None:
        """A 403 here would confirm the id is real — an enumeration oracle."""
        real_other = client.delete(f"{URL}/{rival['entry_id']}", headers=auth_headers)
        invented = client.delete(f"{URL}/{uuid.uuid4()}", headers=auth_headers)
        assert real_other.status_code == invented.status_code == 404
        assert real_other.json() == invented.json()

    def test_each_tenant_sees_only_its_own(
        self, client: TestClient, company, auth_headers, rival
    ) -> None:
        create(client, auth_headers)
        mine = client.get(URL, headers=auth_headers).json()
        theirs = client.get(URL, headers=rival["headers"]).json()
        assert {e["title"] for e in mine} == {ENTRY["title"]}
        assert {e["title"] for e in theirs} == {"Bravo cancellation policy"}


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", URL),
            ("GET", f"{URL}/starters"),
            ("POST", URL),
            ("PATCH", f"{URL}/{uuid.uuid4()}"),
            ("DELETE", f"{URL}/{uuid.uuid4()}"),
        ],
    )
    def test_unauthenticated_is_401(self, client: TestClient, company, method, path) -> None:
        assert client.request(method, path, json={}).status_code == 401

    def test_staff_may_read(self, client: TestClient, db, company, auth_headers) -> None:
        create(client, auth_headers)
        staff = make_staff(db, company, UserRole.STAFF, "staff@acme.test")
        assert client.get(URL, headers=staff).status_code == 200
        assert client.get(f"{URL}/starters", headers=staff).status_code == 200

    def test_staff_may_not_write(self, client: TestClient, db, company, auth_headers) -> None:
        """Entries are company policy the agent quotes — same bar as pricing."""
        entry = create(client, auth_headers)
        staff = make_staff(db, company, UserRole.STAFF, "staff@acme.test")

        assert client.post(URL, json={**ENTRY, "title": "New"}, headers=staff).status_code == 403
        patched = client.patch(
            f"{URL}/{entry['id']}", json={"is_active": False}, headers=staff
        )
        assert patched.status_code == 403
        assert client.delete(f"{URL}/{entry['id']}", headers=staff).status_code == 403

    def test_admin_may_write(self, client: TestClient, db, company, auth_headers) -> None:
        admin = make_staff(db, company, UserRole.ADMIN, "admin@acme.test")
        assert client.post(URL, json=ENTRY, headers=admin).status_code == 201


class TestStarterTopics:
    def test_returns_the_full_template_set(self, client: TestClient, company, auth_headers) -> None:
        body = client.get(f"{URL}/starters", headers=auth_headers).json()
        assert len(body) == len(STARTER_TOPICS) == 12

    def test_templates_carry_no_business_answers(
        self, client: TestClient, company, auth_headers
    ) -> None:
        """The owner supplies every fact; a template is a question and nothing more."""
        for topic in client.get(f"{URL}/starters", headers=auth_headers).json():
            assert set(topic) == {"category", "title", "prompt", "keywords"}
            assert "content" not in topic
            assert topic["prompt"].endswith("?")

    def test_categories_come_from_the_shared_vocabulary(self) -> None:
        assert {topic.category for topic in STARTER_TOPICS} <= set(KNOWLEDGE_CATEGORIES)

    def test_titles_are_unique(self) -> None:
        titles = [topic.title for topic in STARTER_TOPICS]
        assert len(set(titles)) == len(titles)

    def test_covers_the_expected_topics(self) -> None:
        prompts = " ".join(topic.prompt.lower() for topic in STARTER_TOPICS)
        for subject in [
            "insurance",
            "packing",
            "piano",
            "cancellation",
            "reschedul",
            "stairs",
            "elevator",
            "areas",
            "deposit",
            "longer",
            "parking",
            "not included",
        ]:
            assert subject in prompts, subject


class TestAgentIntegration:
    """Step 3A and 3B address the same table — no sync step, no cache."""

    def test_entry_created_in_the_dashboard_is_searchable_by_the_agent(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        create(client, auth_headers)
        lead, request, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)

        result = ToolExecutor(db, context).execute(
            "search_company_knowledge", {"query": "do you provide a COI?"}
        )
        assert result["results"][0]["title"] == ENTRY["title"]
        assert result["results"][0]["content"] == ENTRY["content"]

    def test_deactivating_in_the_dashboard_hides_it_from_the_agent(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        entry = create(client, auth_headers)
        lead, request, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)
        executor = ToolExecutor(db, context)
        assert executor.execute("search_company_knowledge", {"query": "COI"})["results"]

        client.patch(f"{URL}/{entry['id']}", json={"is_active": False}, headers=auth_headers)
        db.expire_all()
        assert executor.execute("search_company_knowledge", {"query": "COI"})["results"] == []

    def test_editing_content_changes_what_the_agent_says(
        self, client: TestClient, db, company, auth_headers
    ) -> None:
        entry = create(client, auth_headers)
        client.patch(
            f"{URL}/{entry['id']}",
            json={"content": "COIs cost $50 and take a week."},
            headers=auth_headers,
        )
        lead, request, quote = make_quote_chain(db, company)
        context = agent_service.build_agent_context(db, quote.public_token)
        db.expire_all()

        result = ToolExecutor(db, context).execute(
            "search_company_knowledge", {"query": "certificate of insurance"}
        )
        assert result["results"][0]["content"] == "COIs cost $50 and take a week."
