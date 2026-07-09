"""Distance provider: driving distance between two US addresses.

The pricing engine needs the driving distance for travel fees and (later) long-distance
pricing. The interface is a single call; implementations:

- :class:`FakeDistanceProvider` — deterministic, network-free. Default in dev/tests.
  Distance is derived from the numeric difference of the two ZIP codes, so identical
  ZIPs are "close" and far-apart ZIPs are "far" — stable and good enough to exercise
  every pricing path.
- ``google`` / ``mapbox`` — real implementations land with the deployment milestone;
  selecting them before that raises a clear configuration error instead of silently
  falling back to fake numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings


@dataclass(frozen=True)
class RoutePoint:
    """Minimal address for distance calculation."""

    line1: str
    city: str
    state: str
    zip: str


@dataclass(frozen=True)
class DistanceResult:
    miles: float
    provider: str


class DistanceProvider(Protocol):
    """Computes driving distance between two points."""

    def distance_miles(self, origin: RoutePoint, destination: RoutePoint) -> DistanceResult:
        """Return the driving distance in miles."""
        ...


class FakeDistanceProvider:
    """Deterministic, offline distance provider for development and tests.

    :param overrides: exact distances keyed by ``(origin_zip, destination_zip)``; use in
        tests that need a specific value.
    """

    def __init__(self, overrides: dict[tuple[str, str], float] | None = None) -> None:
        self._overrides = overrides or {}

    def distance_miles(self, origin: RoutePoint, destination: RoutePoint) -> DistanceResult:
        key = (origin.zip, destination.zip)
        if key in self._overrides:
            return DistanceResult(miles=self._overrides[key], provider="fake")

        if origin.zip == destination.zip:
            miles = 3.0  # same ZIP: short local hop
        else:
            # Stable pseudo-distance from ZIP numeric difference, clamped to a
            # plausible local-move range (5–60 miles).
            delta = abs(_zip_number(origin.zip) - _zip_number(destination.zip))
            miles = float(5 + delta % 56)
        return DistanceResult(miles=miles, provider="fake")


def _zip_number(zip_code: str) -> int:
    digits = "".join(ch for ch in zip_code if ch.isdigit())[:5]
    return int(digits) if digits else 0


class ProviderConfigurationError(RuntimeError):
    """Raised when a configured provider is unknown or not yet available."""


def get_distance_provider(settings: Settings) -> DistanceProvider:
    """Return the distance provider selected by ``settings.geocoding_provider``."""
    name = settings.geocoding_provider.lower()
    if name == "fake":
        return FakeDistanceProvider()
    if name in {"google", "mapbox"}:
        raise ProviderConfigurationError(
            f"Distance provider '{name}' is planned but not implemented yet; "
            "set GEOCODING_PROVIDER=fake"
        )
    raise ProviderConfigurationError(f"Unknown distance provider '{name}'")
