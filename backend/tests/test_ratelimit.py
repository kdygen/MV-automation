"""Tests for per-IP rate limiting (limiter unit tests + endpoint behavior)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.ratelimit import SlidingWindowLimiter
from tests.test_intake_api import make_payload


class TestSlidingWindowLimiter:
    def test_allows_up_to_limit_then_blocks(self) -> None:
        limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60)
        assert [limiter.allow("k", now=t) for t in (0, 1, 2)] == [True, True, True]
        assert limiter.allow("k", now=3) is False

    def test_window_slides(self) -> None:
        limiter = SlidingWindowLimiter(max_requests=2, window_seconds=10)
        assert limiter.allow("k", now=0)
        assert limiter.allow("k", now=5)
        assert limiter.allow("k", now=9) is False  # window [0..9] already holds 2
        assert limiter.allow("k", now=11) is True  # hit at t=0 has expired

    def test_keys_are_independent(self) -> None:
        limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60)
        assert limiter.allow("ip-a", now=0)
        assert limiter.allow("ip-b", now=0)  # different key, own budget
        assert limiter.allow("ip-a", now=1) is False


class TestEndpointLimits:
    def test_submit_returns_429_after_limit(self, client: TestClient, company) -> None:
        """The 21st submission from one IP inside the window is refused."""
        for i in range(20):
            resp = client.post("/api/v1/public/acme-movers/requests", json=make_payload())
            assert resp.status_code == 201, f"request {i + 1} unexpectedly failed"

        resp = client.post("/api/v1/public/acme-movers/requests", json=make_payload())
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "rate_limited"

    def test_different_ip_has_its_own_budget(self, client: TestClient, company) -> None:
        for _ in range(20):
            client.post("/api/v1/public/acme-movers/requests", json=make_payload())
        # Same app instance, but a different forwarded client address.
        resp = client.post(
            "/api/v1/public/acme-movers/requests",
            json=make_payload(),
            headers={"X-Forwarded-For": "203.0.113.77"},
        )
        assert resp.status_code == 201

    def test_reads_are_not_limited(self, client: TestClient, company) -> None:
        token = (
            client.post("/api/v1/public/acme-movers/requests", json=make_payload())
            .json()["quote"]["public_token"]
        )
        for _ in range(40):
            assert client.get(f"/api/v1/public/quotes/{token}").status_code == 200
