"""Tests for the pricing service: config seeding, versioning, request estimation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import Company, Lead, MovingRequest, PricingConfigRow
from app.models.moving_request import HomeSize, PackingService
from app.pricing import RULES_ENGINE_VERSION, PricingConfig, PricingInputError
from app.services import pricing as pricing_service


def make_request(db, company: Company, *, distance: float | None = 12.0) -> MovingRequest:
    lead = Lead(company_id=company.id, name="Bob", email="bob@example.com")
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
        move_date=date.today() + timedelta(days=30),
        home_size=HomeSize.TWO_BR,
        packing_service=PackingService.NONE,
        special_items=[],
        distance_miles=distance,
        raw_payload={},
    )
    db.add(request)
    db.commit()
    return request


def test_ensure_active_config_seeds_default(db, company) -> None:
    row = pricing_service.ensure_active_config(db, company.id)
    assert row.version == 1
    assert row.is_active is True
    assert pricing_service.load_config(row) == PricingConfig()


def test_ensure_active_config_is_idempotent(db, company) -> None:
    first = pricing_service.ensure_active_config(db, company.id)
    second = pricing_service.ensure_active_config(db, company.id)
    assert first.id == second.id
    assert db.scalars(select(PricingConfigRow)).all() == [first]


def test_set_config_appends_new_version_and_deactivates_old(db, company) -> None:
    v1 = pricing_service.ensure_active_config(db, company.id)
    v2 = pricing_service.set_config(
        db, company.id, PricingConfig(travel_fee_base=75.0)
    )
    db.refresh(v1)
    assert v2.version == 2
    assert v2.is_active is True
    assert v1.is_active is False  # kept, not deleted
    active = pricing_service.get_active_config_row(db, company.id)
    assert active is not None and active.id == v2.id
    assert pricing_service.load_config(v2).travel_fee_base == 75.0


def test_configs_are_tenant_scoped(db, company) -> None:
    other = Company(name="Bravo", slug="bravo", settings={})
    db.add(other)
    db.commit()

    pricing_service.set_config(db, company.id, PricingConfig(travel_fee_base=99.0))
    other_row = pricing_service.ensure_active_config(db, other.id)
    assert pricing_service.load_config(other_row).travel_fee_base == 50.0  # default, not 99


def test_estimate_for_request_produces_versioned_estimate(db, company) -> None:
    request = make_request(db, company)
    estimate, config_row = pricing_service.estimate_for_request(db, request)
    assert estimate.engine_version == RULES_ENGINE_VERSION
    assert estimate.total_cents > 0
    assert config_row.company_id == company.id
    assert estimate.inputs["home_size"] == "2br"


def test_estimate_for_request_without_distance_raises(db, company) -> None:
    request = make_request(db, company, distance=None)
    with pytest.raises(PricingInputError):
        pricing_service.estimate_for_request(db, request)


def test_estimate_uses_companys_own_config(db, company) -> None:
    """A pricier config for the same request must produce a pricier estimate."""
    request = make_request(db, company)
    baseline, _ = pricing_service.estimate_for_request(db, request)

    pricing_service.set_config(
        db,
        company.id,
        PricingConfig(hourly_rate_by_crew={2: 300.0, 3: 400.0, 4: 500.0, 5: 600.0}),
    )
    pricier, config_row = pricing_service.estimate_for_request(db, request)
    assert config_row.version == 2
    assert pricier.total_cents > baseline.total_cents
