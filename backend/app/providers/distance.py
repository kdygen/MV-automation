"""Distance provider: driving distance between two US addresses.

The pricing engine needs the driving distance for travel fees and (later) long-distance
pricing. The interface is a single call; implementations:

- :class:`FakeDistanceProvider` — deterministic, network-free. Default in dev/tests.
  Distance is derived from the numeric difference of the two ZIP codes, so identical
  ZIPs are "close" and far-apart ZIPs are "far" — stable and good enough to exercise
  every pricing path.
- :class:`GoogleDistanceProvider` — real driving distance via the Google Distance
  Matrix API. Selected with ``GEOCODING_PROVIDER=google`` + ``GEOCODING_API_KEY``.
- ``mapbox`` — planned; selecting it raises a clear configuration error instead of
  silently falling back to fake numbers.

Lookup failures raise :class:`DistanceLookupError`; the intake service treats distance
as best-effort (a lead is stored without a distance rather than lost).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_GOOGLE_ENDPOINT = "https://maps.googleapis.com/maps/api/distancematrix/json"
_LOOKUP_TIMEOUT_SECONDS = 10.0
_METERS_PER_MILE = 1609.344


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


class DistanceLookupError(RuntimeError):
    """A provider could not resolve a driving distance for the given addresses."""


class GoogleDistanceProvider:
    """Driving distance via the Google Distance Matrix API.

    :param transport: injectable ``httpx`` transport so tests run without network.
    """

    def __init__(self, *, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self._api_key = api_key
        self._transport = transport

    @staticmethod
    def _format(point: RoutePoint) -> str:
        return f"{point.line1}, {point.city}, {point.state} {point.zip}"

    def distance_miles(self, origin: RoutePoint, destination: RoutePoint) -> DistanceResult:
        try:
            with httpx.Client(
                transport=self._transport, timeout=_LOOKUP_TIMEOUT_SECONDS
            ) as client:
                response = client.get(
                    _GOOGLE_ENDPOINT,
                    params={
                        "origins": self._format(origin),
                        "destinations": self._format(destination),
                        "units": "imperial",
                        "key": self._api_key,
                    },
                )
        except httpx.HTTPError as exc:
            raise DistanceLookupError(f"Distance Matrix request failed: {exc}") from exc

        if response.status_code != 200:
            raise DistanceLookupError(
                f"Distance Matrix returned HTTP {response.status_code}"
            )
        body = response.json()
        if body.get("status") != "OK":
            raise DistanceLookupError(f"Distance Matrix status: {body.get('status')}")
        try:
            element = body["rows"][0]["elements"][0]
        except (KeyError, IndexError) as exc:
            raise DistanceLookupError("Distance Matrix response missing elements") from exc
        if element.get("status") != "OK":
            # e.g. NOT_FOUND / ZERO_RESULTS for an unroutable address
            raise DistanceLookupError(f"Route element status: {element.get('status')}")

        meters = element["distance"]["value"]
        miles = round(meters / _METERS_PER_MILE, 1)
        logger.info("Google distance: %.1f mi (%s → %s)", miles, origin.zip, destination.zip)
        return DistanceResult(miles=miles, provider="google")


class ProviderConfigurationError(RuntimeError):
    """Raised when a configured provider is unknown or not yet available."""


def get_distance_provider(settings: Settings) -> DistanceProvider:
    """Return the distance provider selected by ``settings.geocoding_provider``."""
    name = settings.geocoding_provider.lower()
    if name == "fake":
        return FakeDistanceProvider()
    if name == "google":
        if not settings.geocoding_api_key:
            raise ProviderConfigurationError(
                "GEOCODING_PROVIDER=google requires GEOCODING_API_KEY to be set"
            )
        return GoogleDistanceProvider(api_key=settings.geocoding_api_key)
    if name == "mapbox":
        raise ProviderConfigurationError(
            "Distance provider 'mapbox' is planned but not implemented yet; "
            "set GEOCODING_PROVIDER=fake or google"
        )
    raise ProviderConfigurationError(f"Unknown distance provider '{name}'")
