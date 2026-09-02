"""Application configuration.

Settings are loaded from environment variables (and a local ``.env`` file) via
``pydantic-settings``. Access the singleton through :func:`get_settings`, which is
cached so the environment is parsed once per process. Tests override individual values
by constructing :class:`Settings` directly or by monkeypatching the cache.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    """Strongly-typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "MV Automation"
    environment: Environment = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # CORS origins as a comma-separated string in the env; exposed as a list.
    cors_origins: str = "http://localhost:3000"

    # --- Database ---
    database_url: str = "sqlite+pysqlite:///./mv_local.db"

    # --- Supabase Auth ---
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    jwt_algorithm: str = "HS256"

    # --- Providers ---
    geocoding_provider: str = "fake"
    geocoding_api_key: str = ""
    email_provider: str = "fake"
    email_api_key: str = ""
    email_from: str = "quotes@example.com"

    # --- LLM (chat milestone) ---
    anthropic_api_key: str = ""

    # --- Hardening ---
    rate_limit_enabled: bool = True
    sentry_dsn: str = ""  # empty = Sentry disabled

    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _require_real_database_in_production(self) -> Settings:
        """Refuse to start in production without an explicit PostgreSQL database.

        ``database_url`` defaults to a local SQLite file for zero-setup development.
        In production that default would silently boot the app on a throwaway file
        database — data loss waiting to happen — so we fail loudly instead.
        """
        if self.environment == "production" and not self.database_url.startswith("postgresql"):
            raise ValueError(
                "ENVIRONMENT=production requires DATABASE_URL to point at PostgreSQL "
                f"(got: {self.database_url.split('://')[0]}://...). Set DATABASE_URL to "
                "your Supabase connection string."
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins parsed into a list of trimmed, non-empty entries."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def supabase_jwks_url(self) -> str | None:
        """The project's public JWKS endpoint (ES256 token verification), if configured."""
        if not self.supabase_url:
            return None
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
