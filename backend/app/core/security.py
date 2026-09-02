"""JWT verification for Supabase Auth.

The frontend authenticates users with Supabase Auth and sends the resulting access
token as ``Authorization: Bearer <jwt>``. Supabase signs these tokens one of two ways:

- **ES256 (asymmetric)** — the default for new projects. Tokens are signed with a
  private key held by Supabase and verified against the project's public **JWKS**
  endpoint (``<SUPABASE_URL>/auth/v1/.well-known/jwks.json``).
- **HS256 (legacy shared secret)** — older projects, and our local-dev token flow
  (``scripts/mint_dev_token.py``), sign with a shared secret.

:func:`decode_access_token` dispatches on the token's declared algorithm, verifies the
signature, audience, and expiry, and returns the claims. Mapping the token's ``sub``
(Supabase user id) to a platform user and company happens in the API dependency layer,
not here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import jwt

from app.core.errors import AuthError


@lru_cache
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    """One cached JWKS client per URL (PyJWKClient caches the fetched keys)."""
    return jwt.PyJWKClient(jwks_url, cache_keys=True)


def decode_access_token(
    token: str,
    *,
    secret: str,
    algorithms: list[str] | None = None,
    audience: str = "authenticated",
    jwks_url: str | None = None,
) -> dict[str, Any]:
    """Verify and decode a Supabase access token (ES256 via JWKS, or HS256 via secret).

    :raises AuthError: if verification is unconfigured for the token's algorithm, or
        the token is invalid/expired.
    """
    try:
        header_alg = jwt.get_unverified_header(token).get("alg")
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid access token", code="invalid_token") from exc

    try:
        if header_alg == "ES256":
            if not jwks_url:
                raise AuthError(
                    "Authentication is not configured", code="auth_not_configured"
                )
            signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key,
                algorithms=["ES256"],
                audience=audience,
                options={"require": ["exp", "sub"]},
            )
        else:
            if not secret:
                # Misconfiguration, not a client error; surfaced as 401 to avoid leaking.
                raise AuthError(
                    "Authentication is not configured", code="auth_not_configured"
                )
            claims = jwt.decode(
                token,
                secret,
                algorithms=algorithms or ["HS256"],
                audience=audience,
                options={"require": ["exp", "sub"]},
            )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Access token has expired", code="token_expired") from exc
    except jwt.PyJWKClientError as exc:
        raise AuthError("Invalid access token", code="invalid_token") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid access token", code="invalid_token") from exc

    return claims
