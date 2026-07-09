"""Unit tests for JWT verification (app.core.security)."""

import uuid
from datetime import timedelta

import pytest

from app.core.errors import AuthError
from app.core.security import decode_access_token
from tests.conftest import TEST_JWT_SECRET, mint_token


def test_valid_token_decodes() -> None:
    user_id = uuid.uuid4()
    token = mint_token(user_id)
    claims = decode_access_token(token, secret=TEST_JWT_SECRET)
    assert claims["sub"] == str(user_id)
    assert claims["aud"] == "authenticated"


def test_expired_token_rejected() -> None:
    token = mint_token(uuid.uuid4(), expires_in=timedelta(seconds=-10))
    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, secret=TEST_JWT_SECRET)
    assert exc_info.value.code == "token_expired"


def test_wrong_secret_rejected() -> None:
    token = mint_token(uuid.uuid4(), secret="some-other-secret-0123456789abcdef")
    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, secret=TEST_JWT_SECRET)
    assert exc_info.value.code == "invalid_token"


def test_wrong_audience_rejected() -> None:
    token = mint_token(uuid.uuid4(), audience="something-else")
    with pytest.raises(AuthError):
        decode_access_token(token, secret=TEST_JWT_SECRET)


def test_missing_secret_rejected() -> None:
    token = mint_token(uuid.uuid4())
    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, secret="")
    assert exc_info.value.code == "auth_not_configured"


def test_garbage_token_rejected() -> None:
    with pytest.raises(AuthError):
        decode_access_token("not-a-jwt", secret=TEST_JWT_SECRET)
