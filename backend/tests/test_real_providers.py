"""Tests for the real (network-backed) providers, using mock HTTP transports.

No test here touches the network: ``httpx.MockTransport`` intercepts every request so
we can assert on exactly what would be sent to Resend / Google and simulate their
responses, including failures.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.providers.distance import (
    DistanceLookupError,
    FakeDistanceProvider,
    GoogleDistanceProvider,
    ProviderConfigurationError,
    RoutePoint,
    get_distance_provider,
)
from app.providers.email import (
    EmailConfigurationError,
    EmailSendError,
    FakeEmailProvider,
    ResendEmailProvider,
    get_email_provider,
)

ORIGIN = RoutePoint(line1="12 Elm St", city="Springfield", state="IL", zip="62701")
DEST = RoutePoint(line1="99 Oak Ave", city="Chatham", state="IL", zip="62629")


class TestResendProvider:
    def test_sends_correct_payload(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "email_123"})

        provider = ResendEmailProvider(
            api_key="re_test_key",
            from_address="quotes@acme.com",
            transport=httpx.MockTransport(handler),
        )
        provider.send(to="bob@example.com", subject="Your quote", text_body="Hello Bob")

        assert captured["url"] == "https://api.resend.com/emails"
        assert captured["auth"] == "Bearer re_test_key"
        assert captured["body"] == {
            "from": "quotes@acme.com",
            "to": ["bob@example.com"],
            "subject": "Your quote",
            "text": "Hello Bob",
        }

    def test_api_rejection_raises_email_send_error(self) -> None:
        provider = ResendEmailProvider(
            api_key="bad_key",
            from_address="quotes@acme.com",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(401, json={"message": "invalid key"})
            ),
        )
        with pytest.raises(EmailSendError, match="401"):
            provider.send(to="bob@example.com", subject="x", text_body="y")

    def test_network_failure_raises_email_send_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        provider = ResendEmailProvider(
            api_key="k", from_address="a@b.c", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(EmailSendError, match="request failed"):
            provider.send(to="bob@example.com", subject="x", text_body="y")


class TestGoogleDistanceProvider:
    @staticmethod
    def _ok_response(meters: int) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "rows": [
                    {
                        "elements": [
                            {"status": "OK", "distance": {"value": meters, "text": "x"}}
                        ]
                    }
                ],
            },
        )

    def test_parses_miles_and_sends_addresses(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return self._ok_response(meters=16093)  # ≈ 10 miles

        provider = GoogleDistanceProvider(
            api_key="g_key", transport=httpx.MockTransport(handler)
        )
        result = provider.distance_miles(ORIGIN, DEST)

        assert result.miles == 10.0
        assert result.provider == "google"
        assert captured["params"]["origins"] == "12 Elm St, Springfield, IL 62701"
        assert captured["params"]["destinations"] == "99 Oak Ave, Chatham, IL 62629"
        assert captured["params"]["key"] == "g_key"

    def test_unroutable_address_raises_lookup_error(self) -> None:
        response = httpx.Response(
            200,
            json={"status": "OK", "rows": [{"elements": [{"status": "NOT_FOUND"}]}]},
        )
        provider = GoogleDistanceProvider(
            api_key="k", transport=httpx.MockTransport(lambda r: response)
        )
        with pytest.raises(DistanceLookupError, match="NOT_FOUND"):
            provider.distance_miles(ORIGIN, DEST)

    def test_api_level_error_raises_lookup_error(self) -> None:
        response = httpx.Response(200, json={"status": "REQUEST_DENIED", "rows": []})
        provider = GoogleDistanceProvider(
            api_key="k", transport=httpx.MockTransport(lambda r: response)
        )
        with pytest.raises(DistanceLookupError, match="REQUEST_DENIED"):
            provider.distance_miles(ORIGIN, DEST)

    def test_network_failure_raises_lookup_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        provider = GoogleDistanceProvider(api_key="k", transport=httpx.MockTransport(handler))
        with pytest.raises(DistanceLookupError, match="request failed"):
            provider.distance_miles(ORIGIN, DEST)


class TestProviderRegistries:
    def test_resend_selected_with_key(self) -> None:
        settings = Settings(
            email_provider="resend", email_api_key="re_123", _env_file=None
        )
        assert isinstance(get_email_provider(settings), ResendEmailProvider)

    def test_resend_without_key_is_config_error(self) -> None:
        settings = Settings(email_provider="resend", email_api_key="", _env_file=None)
        with pytest.raises(EmailConfigurationError, match="EMAIL_API_KEY"):
            get_email_provider(settings)

    def test_google_selected_with_key(self) -> None:
        settings = Settings(
            geocoding_provider="google", geocoding_api_key="g_123", _env_file=None
        )
        assert isinstance(get_distance_provider(settings), GoogleDistanceProvider)

    def test_google_without_key_is_config_error(self) -> None:
        settings = Settings(
            geocoding_provider="google", geocoding_api_key="", _env_file=None
        )
        with pytest.raises(ProviderConfigurationError, match="GEOCODING_API_KEY"):
            get_distance_provider(settings)

    def test_fakes_remain_default(self, test_settings: Settings) -> None:
        assert isinstance(get_email_provider(test_settings), FakeEmailProvider)
        assert isinstance(get_distance_provider(test_settings), FakeDistanceProvider)
