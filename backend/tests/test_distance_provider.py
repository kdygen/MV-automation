"""Unit tests for the distance provider layer."""

import pytest

from app.core.config import Settings
from app.providers.distance import (
    FakeDistanceProvider,
    ProviderConfigurationError,
    RoutePoint,
    get_distance_provider,
)


def _point(zip_code: str) -> RoutePoint:
    return RoutePoint(line1="1 Main St", city="Springfield", state="IL", zip=zip_code)


def test_same_zip_is_short_hop() -> None:
    provider = FakeDistanceProvider()
    result = provider.distance_miles(_point("62701"), _point("62701"))
    assert result.miles == 3.0
    assert result.provider == "fake"


def test_distance_is_deterministic() -> None:
    provider = FakeDistanceProvider()
    a = provider.distance_miles(_point("62701"), _point("62704"))
    b = provider.distance_miles(_point("62701"), _point("62704"))
    assert a.miles == b.miles
    assert 5 <= a.miles <= 60


def test_overrides_win() -> None:
    provider = FakeDistanceProvider(overrides={("62701", "60601"): 205.5})
    result = provider.distance_miles(_point("62701"), _point("60601"))
    assert result.miles == 205.5


def test_registry_returns_fake(test_settings: Settings) -> None:
    assert isinstance(get_distance_provider(test_settings), FakeDistanceProvider)


def test_registry_rejects_unimplemented_provider() -> None:
    settings = Settings(geocoding_provider="mapbox", _env_file=None)
    with pytest.raises(ProviderConfigurationError, match="not implemented"):
        get_distance_provider(settings)


def test_registry_rejects_unknown_provider() -> None:
    settings = Settings(geocoding_provider="carrier-pigeon", _env_file=None)
    with pytest.raises(ProviderConfigurationError, match="Unknown"):
        get_distance_provider(settings)
