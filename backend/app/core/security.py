"""JWT verification for Supabase Auth.

The frontend authenticates users with Supabase Auth and sends the resulting access
token as ``Authorization: Bearer <jwt>``. Supabase signs these tokens with the project's
JWT secret (HS256). :func:`decode_access_token` verifies the signature, audience, and
expiry and returns the claims. Mapping the token's ``sub`` (Supabase user id) to a
platform user and company happens in the API dependency layer, not here.
"""

from __future__ import annotations

from typing import Any

import jwt

from app.core.errors import AuthError


def decode_access_token(
    token: str,
    *,
    secret: str,
    algorithms: list[str] | None = None,
    audience: str = "authenticated",
) -> dict[str, Any]:
    """Verify and decode a Supabase access token.

    :raises AuthError: if the secret is unset, or the token is invalid/expired.
    """
    if not secret:
        # Misconfiguration, not a client error, but surfaced as 401 to avoid leaking.
        raise AuthError("Authentication is not configured", code="auth_not_configured")

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=algorithms or ["HS256"],
            audience=audience,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Access token has expired", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid access token", code="invalid_token") from exc

    return claims
