"""Application configuration.

Settings are loaded from environment variables (and a local ``.env`` file) via
``pydantic-settings``. Access the singleton through :func:`get_settings`, which is
cached so the environment is parsed once per process. Tests override individual values
by constructing :class:`Settings` directly or by monkeypatching the cache.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
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

    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, value: str) -> str:
        return value.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins parsed into a list of trimmed, non-empty entries."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
