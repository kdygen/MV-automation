"""Shared API dependencies: authentication and role enforcement.

``get_current_user`` verifies the Supabase JWT and resolves it to a platform user,
returning a :class:`CurrentUser` that carries the tenant (``company_id``) used to scope
every dashboard query. Tests override ``get_current_user`` to inject a fixed identity
without minting real tokens.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AuthError, ForbiddenError
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.db.session import get_db, get_sessionmaker
from app.models.user import User, UserRole
from app.providers.distance import DistanceProvider, get_distance_provider
from app.providers.email import FakeEmailProvider, ResendEmailProvider, get_email_provider
from app.providers.embeddings import EmbeddingProvider, resolve_embedding_provider
from app.providers.payment import (
    FakePaymentProvider,
    StripePaymentProvider,
    get_payment_provider,
)

# What the email dependency can hand out (fake in dev/tests, Resend in production).
AnyEmailProvider = FakeEmailProvider | ResendEmailProvider
# Fake offline in dev and every test; Stripe in production.
AnyPaymentProvider = FakePaymentProvider | StripePaymentProvider

logger = get_logger(__name__)

#: A session factory for work that outlives the request that started it.
SessionScope = Callable[[], AbstractContextManager[Session]]

_bearer = HTTPBearer(auto_error=False)


def distance_provider_dep(settings: Settings = Depends(get_settings)) -> DistanceProvider:
    """Resolve the configured distance provider (overridden in tests)."""
    return get_distance_provider(settings)


def embedding_provider_dep(
    settings: Settings = Depends(get_settings),
) -> EmbeddingProvider | None:
    """Resolve the configured embedding provider, or ``None`` if it is not usable.

    The tolerance rule lives in :func:`resolve_embedding_provider` so the request path
    and the agent's retrieval path cannot drift apart on what a missing key means.
    """
    return resolve_embedding_provider(settings)


@contextmanager
def _new_session() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def session_scope_dep() -> SessionScope:
    """Hand back a factory for sessions that background work can own.

    The request's own session from :func:`get_db` is closed as soon as the response is
    sent, so a background task must open its own. Injecting the factory rather than
    calling :func:`get_sessionmaker` directly is what lets the test suite point ingestion
    at the same in-memory database the request used.
    """
    return _new_session


def email_provider_dep(settings: Settings = Depends(get_settings)) -> AnyEmailProvider:
    """Resolve the configured email provider (overridden in tests)."""
    return get_email_provider(settings)


def payment_provider_dep(settings: Settings = Depends(get_settings)) -> AnyPaymentProvider:
    """Resolve the configured payment provider (overridden in tests)."""
    return get_payment_provider(settings)


def quote_public_url(settings: Settings, token: str) -> str:
    """Customer-facing quote link, rooted at the frontend origin."""
    base = settings.cors_origin_list[0] if settings.cors_origin_list else ""
    return f"{base}/quote/{token}"


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated staff member and their tenant."""

    id: uuid.UUID
    company_id: uuid.UUID
    email: str
    role: UserRole


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Resolve the bearer token to a platform user, or raise ``AuthError``."""
    if credentials is None or not credentials.credentials:
        raise AuthError("Missing bearer token")

    claims = decode_access_token(
        credentials.credentials,
        secret=settings.supabase_jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.supabase_jwt_audience,
        jwks_url=settings.supabase_jwks_url,
    )

    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise AuthError("Token missing a valid subject") from exc

    user = db.get(User, user_id)
    if user is None:
        # Authenticated with Supabase but not provisioned as a platform user.
        raise ForbiddenError("User is not provisioned for any company")

    return CurrentUser(
        id=user.id, company_id=user.company_id, email=user.email, role=user.role
    )


def require_roles(*allowed: UserRole) -> Callable[..., CurrentUser]:
    """Build a dependency that requires the current user to hold one of ``allowed``."""

    def _dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise ForbiddenError("Insufficient role for this action")
        return user

    return _dependency


# Convenience: fetch a User row still bound to the session (rarely needed by routers).
def load_user(db: Session, user_id: uuid.UUID) -> User | None:
    return db.scalar(select(User).where(User.id == user_id))
