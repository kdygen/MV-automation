"""Tests for ES256 (Supabase asymmetric signing keys) token verification.

Tokens are signed with a locally generated P-256 key pair; the JWKS client is replaced
with a stub returning the matching public key, so no network is involved.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import security
from app.core.errors import AuthError
from app.core.security import decode_access_token

JWKS_URL = "https://example.supabase.co/auth/v1/.well-known/jwks.json"


@pytest.fixture(scope="module")
def key_pair() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def mint_es256(
    private_key: ec.EllipticCurvePrivateKey,
    *,
    audience: str = "authenticated",
    expires_in: timedelta = timedelta(hours=1),
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": audience,
            "iat": now,
            "exp": now + expires_in,
            "role": "authenticated",
        },
        private_key,
        algorithm="ES256",
    )


@pytest.fixture()
def stub_jwks(monkeypatch: pytest.MonkeyPatch, key_pair: ec.EllipticCurvePrivateKey) -> None:
    """Make the JWKS client return our test public key instead of fetching."""

    class StubClient:
        def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
            return SimpleNamespace(key=key_pair.public_key())

    monkeypatch.setattr(security, "_jwks_client", lambda url: StubClient())


def test_valid_es256_token_decodes(stub_jwks, key_pair) -> None:
    token = mint_es256(key_pair)
    claims = decode_access_token(token, secret="", jwks_url=JWKS_URL)
    assert claims["aud"] == "authenticated"
    assert "sub" in claims


def test_expired_es256_token_rejected(stub_jwks, key_pair) -> None:
    token = mint_es256(key_pair, expires_in=timedelta(seconds=-5))
    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, secret="", jwks_url=JWKS_URL)
    assert exc_info.value.code == "token_expired"


def test_wrong_audience_es256_rejected(stub_jwks, key_pair) -> None:
    token = mint_es256(key_pair, audience="something-else")
    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, secret="", jwks_url=JWKS_URL)
    assert exc_info.value.code == "invalid_token"


def test_es256_signed_by_other_key_rejected(stub_jwks) -> None:
    imposter = ec.generate_private_key(ec.SECP256R1())
    token = mint_es256(imposter)
    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, secret="", jwks_url=JWKS_URL)
    assert exc_info.value.code == "invalid_token"


def test_es256_without_jwks_url_is_unconfigured(key_pair) -> None:
    token = mint_es256(key_pair)
    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, secret="some-secret", jwks_url=None)
    assert exc_info.value.code == "auth_not_configured"


def test_hs256_path_still_works_alongside(stub_jwks) -> None:
    """Local-dev HS256 tokens keep working — algorithm dispatch is per-token."""
    from tests.conftest import TEST_JWT_SECRET, mint_token

    claims = decode_access_token(
        mint_token(uuid.uuid4()), secret=TEST_JWT_SECRET, jwks_url=JWKS_URL
    )
    assert claims["aud"] == "authenticated"


def test_supabase_jwks_url_derivation() -> None:
    from app.core.config import Settings

    s = Settings(supabase_url="https://abc.supabase.co", _env_file=None)
    assert s.supabase_jwks_url == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    assert Settings(supabase_url="", _env_file=None).supabase_jwks_url is None
