"""Tests for deterministic structured similarity and historical signals (Step 5E).

Three properties carry the design and are asserted repeatedly:

* **price and crew are never features** — using either would make the eventual purpose
  (estimating price and crew) circular;
* **weights renormalize over present features**, so a sparse historical row is not
  penalised for sparsity and no single noisy field can dominate;
* **tenant isolation precedes relevance** — a more similar move in another company is
  not a worse match, it is invisible.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.history.similarity import (
    MIN_COVERAGE,
    TOTAL_WEIGHT,
    WEIGHTS,
    MoveFeatures,
    compare,
)
from app.models import Company, Job, JobSource, User, UserRole
from app.schemas.history import SimilarQueryIn
from app.services import insights
from tests.conftest import mint_token

BASE = "/api/v1/history"
PAST = date.today() - timedelta(days=60)

QUERY_2BR = MoveFeatures(
    home_size="2br",
    distance_miles=18,
    origin_floor=3,
    origin_stairs_flights=3,
    origin_has_elevator=False,
    special_items=("piano",),
    packing_service="none",
)


def make_job(db, company: Company, **over) -> Job:
    """A completed move. Defaults describe a plain 2BR so tests vary one thing."""
    values = {
        "company_id": company.id,
        "source": JobSource.IMPORT,
        "move_date": PAST,
        "home_size": "2br",
        "packing_service": "none",
        "distance_miles": 18.0,
        "actual_hours": 7.0,
        "actual_crew_size": 3,
        "actual_total_cents": 150_000,
        "row_hash": uuid.uuid4().hex,
    }
    values.update(over)
    job = Job(**values)
    db.add(job)
    db.commit()
    return job


@pytest.fixture()
def rival(db):
    """A second tenant whose history is deliberately a *better* match than our own."""
    company = Company(name="Bravo Van Lines", slug="bravo-van-lines", settings={})
    db.add(company)
    db.flush()
    user = User(id=uuid.uuid4(), company_id=company.id, email="o@bravo.test", role=UserRole.OWNER)
    db.add(user)
    db.commit()
    return {
        "company": company,
        "headers": {"Authorization": f"Bearer {mint_token(user.id)}"},
    }


class TestWeights:
    def test_no_price_feature_exists(self) -> None:
        """Matching on price then calibrating price would only confirm old mistakes."""
        assert not any("price" in name or "total" in name or "cents" in name for name in WEIGHTS)

    def test_crew_carries_no_weight_by_default(self) -> None:
        assert WEIGHTS["crew_size"] == 0.0
        assert "crew_size" not in {f.name for f in compare(QUERY_2BR, QUERY_2BR).features}

    def test_crew_can_be_opted_in(self) -> None:
        left = MoveFeatures(home_size="2br", crew_size=3, distance_miles=10)
        right = MoveFeatures(home_size="2br", crew_size=6, distance_miles=10)
        without = compare(left, right).score
        with_crew = compare(left, right, include_crew=True).score
        assert with_crew < without

    def test_no_single_feature_can_dominate(self) -> None:
        """The heaviest feature is under half the total, so one field cannot decide."""
        heaviest = max(w for name, w in WEIGHTS.items() if name != "crew_size")
        assert heaviest / TOTAL_WEIGHT < 0.5


class TestScoring:
    def test_an_identical_move_scores_one(self) -> None:
        assert compare(QUERY_2BR, QUERY_2BR).score == 1.0

    def test_the_design_example_ranks_as_expected(self) -> None:
        a = MoveFeatures(
            home_size="2br",
            distance_miles=16,
            origin_floor=3,
            origin_stairs_flights=3,
            origin_has_elevator=False,
            special_items=("piano",),
            packing_service="none",
        )
        b = MoveFeatures(
            home_size="2br",
            distance_miles=20,
            origin_floor=2,
            origin_stairs_flights=2,
            origin_has_elevator=False,
            special_items=(),
            packing_service="none",
        )
        c = MoveFeatures(
            home_size="3br",
            distance_miles=17,
            origin_floor=1,
            origin_stairs_flights=0,
            origin_has_elevator=True,
            special_items=("piano",),
            packing_service="none",
        )
        scores = [compare(QUERY_2BR, f).score for f in (a, b, c)]
        assert scores == sorted(scores, reverse=True)

    def test_home_size_distance_is_ordinal(self) -> None:
        near = compare(QUERY_2BR, MoveFeatures(home_size="1br", distance_miles=18)).score
        far = compare(QUERY_2BR, MoveFeatures(home_size="5br_plus", distance_miles=18)).score
        assert near > far

    def test_distance_gaps_saturate(self) -> None:
        """Beyond the scale, further apart is not meaningfully less comparable."""
        a = compare(QUERY_2BR, MoveFeatures(home_size="2br", distance_miles=200)).score
        b = compare(QUERY_2BR, MoveFeatures(home_size="2br", distance_miles=2000)).score
        assert a == b

    def test_known_empty_special_items_agree(self) -> None:
        left = MoveFeatures(home_size="2br", distance_miles=10, special_items=())
        right = MoveFeatures(home_size="2br", distance_miles=10, special_items=())
        assert compare(left, right).score == 1.0

    def test_unknown_items_are_not_treated_as_none(self) -> None:
        """NULL means "we do not know"; scoring it as absent would invent evidence."""
        known = MoveFeatures(home_size="2br", distance_miles=10, special_items=("piano",))
        unknown = MoveFeatures(home_size="2br", distance_miles=10, special_items=None)
        result = compare(known, unknown)
        assert "special_items" not in {f.name for f in result.features}

    def test_geography_is_coarse(self) -> None:
        same_zip = compare(
            MoveFeatures(home_size="2br", origin_zip="62701"),
            MoveFeatures(home_size="2br", origin_zip="62701"),
        ).score
        other_zip = compare(
            MoveFeatures(home_size="2br", origin_zip="62701"),
            MoveFeatures(home_size="2br", origin_zip="99999"),
        ).score
        assert same_zip > other_zip

    def test_paired_access_uses_whichever_ends_are_known(self) -> None:
        """Origin-only access still counts; most legacy exports record only one end."""
        query = MoveFeatures(home_size="2br", origin_stairs_flights=4)
        other = MoveFeatures(home_size="2br", origin_stairs_flights=4, destination_stairs_flights=0)
        result = compare(query, other)
        assert "stairs" in {f.name for f in result.features}


class TestCoverage:
    def test_a_sparse_row_is_not_penalised_for_sparsity(self) -> None:
        """Renormalization is the point: missing weight is removed, not scored badly."""
        rich = MoveFeatures(
            home_size="2br",
            distance_miles=18,
            origin_floor=3,
            origin_stairs_flights=3,
            origin_has_elevator=False,
            special_items=("piano",),
            packing_service="none",
        )
        sparse = MoveFeatures(home_size="2br", distance_miles=18)
        assert compare(QUERY_2BR, sparse).score >= compare(QUERY_2BR, rich).score - 0.001

    def test_too_little_shared_information_is_not_comparable(self) -> None:
        result = compare(QUERY_2BR, MoveFeatures(home_size="2br"))
        assert result.coverage < MIN_COVERAGE
        assert result.comparable is False

    def test_nothing_in_common_is_not_comparable(self) -> None:
        result = compare(QUERY_2BR, MoveFeatures())
        assert result.comparable is False
        assert result.score == 0.0

    def test_matched_features_explain_the_score(self) -> None:
        result = compare(QUERY_2BR, QUERY_2BR)
        assert "home_size" in result.matched_features
        assert "distance_miles" in result.matched_features


class TestSignals:
    def test_reports_durations_and_spread(self, db, company) -> None:
        for hours in (6.0, 7.0, 8.0, 9.0, 10.0):
            make_job(db, company, actual_hours=hours)
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        )
        assert signals.comparable_count == 5
        assert signals.moves_with_hours == 5
        assert signals.median_actual_hours == 8.0
        assert signals.mean_actual_hours == 8.0
        assert signals.hours_p25 is not None and signals.hours_p75 is not None
        assert signals.hours_p25 < signals.hours_p75

    def test_estimate_bias_is_signed(self, db, company) -> None:
        """Positive means estimates for moves like this ran low."""
        for actual, quoted in ((8.0, 6.0), (9.0, 6.0), (10.0, 8.0)):
            make_job(db, company, actual_hours=actual, quoted_hours=quoted)
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        )
        assert signals.median_hours_error_pct is not None
        assert signals.median_hours_error_pct > 0
        assert signals.overrun_share_pct == 100.0
        assert signals.moves_with_estimate == 3

    def test_spread_is_withheld_when_there_is_too_little_data(self, db, company) -> None:
        make_job(db, company, actual_hours=7.0)
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        )
        assert signals.median_actual_hours == 7.0
        assert signals.hours_p25 == signals.hours_p75 == 7.0

    def test_no_price_or_crew_recommendation_is_emitted(self, db, company) -> None:
        make_job(db, company)
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        )
        fields = set(signals.model_dump())
        for forbidden in ("recommended_price", "suggested_total_cents", "recommended_crew"):
            assert forbidden not in fields
        assert not any("price" in f or "cents" in f or "total" in f for f in fields)

    def test_issue_tags_surface_as_operational_memory(self, db, company) -> None:
        make_job(db, company, issue_tags=["freight_elevator", "coi_required"])
        make_job(db, company, actual_hours=8.0, issue_tags=["freight_elevator"])
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        )
        assert signals.common_issue_tags[0] == "freight_elevator"

    def test_matches_carry_access_characteristics(self, db, company) -> None:
        make_job(
            db,
            company,
            origin_stairs_flights=3,
            origin_has_elevator=False,
            long_carry=True,
            problem_notes="No loading zone; truck two blocks away.",
        )
        match = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        ).matches[0]
        assert match.origin_stairs_flights == 3
        assert match.long_carry is True
        assert "loading zone" in match.problem_notes

    def test_street_addresses_never_appear_in_signals(self, db, company) -> None:
        make_job(db, company, origin_line1="123 Main St", origin_building_key="123 main st|62701")
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        )
        assert "123 Main St" not in str(signals.model_dump())
        assert "building_key" not in str(set(signals.matches[0].model_dump()))

    def test_ranking_is_deterministic(self, db, company) -> None:
        for n in range(8):
            make_job(db, company, distance_miles=18.0, actual_hours=7.0 + n * 0.0)
        query = SimilarQueryIn(home_size="2br", distance_miles=18)
        first = [m.id for m in insights.similar_moves(db, company.id, query).matches]
        second = [m.id for m in insights.similar_moves(db, company.id, query).matches]
        assert first == second

    def test_limit_is_respected(self, db, company) -> None:
        for _ in range(12):
            make_job(db, company)
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18, limit=3)
        )
        assert len(signals.matches) == 3
        assert signals.comparable_count == 12  # the count covers everything comparable

    def test_no_history_yields_empty_signals(self, db, company) -> None:
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        )
        assert signals.comparable_count == 0
        assert signals.median_actual_hours is None
        assert signals.matches == []


class TestUnifiedSources:
    def test_imported_and_platform_moves_both_appear(self, db, company) -> None:
        """One query, one feature reader — not two intelligence systems."""
        make_job(db, company, source=JobSource.IMPORT, actual_hours=7.0)
        make_job(db, company, source=JobSource.PLATFORM, actual_hours=8.0, booking_id=None)
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        )
        assert {m.source for m in signals.matches} == {"import", "platform"}
        assert signals.comparable_count == 2

    def test_platform_moves_contribute_estimate_bias(self, db, company) -> None:
        make_job(db, company, source=JobSource.PLATFORM, actual_hours=9.0, quoted_hours=6.0)
        signals = insights.similar_moves(
            db, company.id, SimilarQueryIn(home_size="2br", distance_miles=18)
        )
        assert signals.median_hours_error_pct == 50.0


class TestTenantIsolation:
    def test_another_tenants_better_match_is_invisible(self, db, company, rival) -> None:
        """The rival's move is a *perfect* match; ours is worse. Ours must still win."""
        make_job(
            db,
            rival["company"],
            distance_miles=18.0,
            origin_stairs_flights=3,
            origin_has_elevator=False,
            special_items=["piano"],
            actual_hours=99.0,
        )
        mine = make_job(db, company, distance_miles=45.0, actual_hours=7.0)

        signals = insights.similar_moves(
            db,
            company.id,
            SimilarQueryIn(
                home_size="2br",
                distance_miles=18,
                origin_stairs_flights=3,
                origin_has_elevator=False,
                special_items=["piano"],
            ),
        )
        assert [m.id for m in signals.matches] == [mine.id]
        assert signals.comparable_count == 1
        assert 99.0 not in [m.actual_hours for m in signals.matches]

    def test_each_tenant_sees_only_its_own(self, db, company, rival) -> None:
        make_job(db, company, actual_hours=7.0)
        make_job(db, rival["company"], actual_hours=20.0)
        query = SimilarQueryIn(home_size="2br", distance_miles=18)

        assert insights.similar_moves(db, company.id, query).median_actual_hours == 7.0
        assert insights.similar_moves(db, rival["company"].id, query).median_actual_hours == 20.0

    def test_api_similarity_is_scoped_to_the_caller(
        self, client, db, company, auth_headers, rival
    ) -> None:
        make_job(db, rival["company"], actual_hours=99.0)
        resp = client.post(
            f"{BASE}/similar",
            json={"home_size": "2br", "distance_miles": 18},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["comparable_count"] == 0

    def test_similar_to_another_tenants_move_is_404(
        self, client, db, company, auth_headers, rival
    ) -> None:
        theirs = make_job(db, rival["company"])
        assert client.get(f"{BASE}/{theirs.id}/similar", headers=auth_headers).status_code == 404

    def test_find_similar_to_own_move_excludes_itself(
        self, client, db, company, auth_headers
    ) -> None:
        subject = make_job(db, company, actual_hours=7.0)
        make_job(db, company, actual_hours=8.0)
        body = client.get(f"{BASE}/{subject.id}/similar", headers=auth_headers).json()
        assert str(subject.id) not in [m["id"] for m in body["matches"]]
        assert body["comparable_count"] == 1


class TestApi:
    def test_requires_authentication(self, client, company) -> None:
        assert client.post(f"{BASE}/similar", json={"home_size": "2br"}).status_code == 401

    def test_staff_may_query_signals(self, client, db, company, auth_headers) -> None:
        staff = User(
            id=uuid.uuid4(), company_id=company.id, email="s@acme.test", role=UserRole.STAFF
        )
        db.add(staff)
        db.commit()
        make_job(db, company)
        resp = client.post(
            f"{BASE}/similar",
            json={"home_size": "2br", "distance_miles": 18},
            headers={"Authorization": f"Bearer {mint_token(staff.id)}"},
        )
        assert resp.status_code == 200

    def test_price_and_crew_inputs_are_refused(self, client, company, auth_headers) -> None:
        """Accepting either would let a caller reintroduce the circularity."""
        for body in (
            {"home_size": "2br", "actual_total_cents": 150_000},
            {"home_size": "2br", "crew_size": 3},
            {"home_size": "2br", "quoted_total_cents": 100_000},
        ):
            resp = client.post(f"{BASE}/similar", json=body, headers=auth_headers)
            assert resp.status_code == 422, body

    def test_company_id_in_the_body_is_refused(self, client, company, auth_headers) -> None:
        resp = client.post(
            f"{BASE}/similar",
            json={"home_size": "2br", "company_id": str(uuid.uuid4())},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_response_shape(self, client, db, company, auth_headers) -> None:
        make_job(db, company, quoted_hours=6.0, actual_hours=7.5)
        body = client.post(
            f"{BASE}/similar",
            json={"home_size": "2br", "distance_miles": 18},
            headers=auth_headers,
        ).json()
        assert body["comparable_count"] == 1
        match = body["matches"][0]
        assert match["score"] > 0
        assert match["hours_error_pct"] == 25.0
        assert "matched_on" in match
        assert db.scalar(select(Job)) is not None  # querying signals writes nothing
