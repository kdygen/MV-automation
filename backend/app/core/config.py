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

    # --- Embeddings / semantic knowledge ---
    #: "fake" (offline, used by every test and local development) or "openai".
    embedding_provider: str = "fake"
    embedding_model: str = "text-embedding-3-small"
    #: Cosine floor below which a vector match is not evidence.
    #:
    #: Measured, not guessed. On the 45-query labelled fixture with
    #: ``text-embedding-3-small``, against the Step 3A keyword retrieval that production
    #: serves today (R@3 0.86, MRR 0.804, FPR 0.41):
    #:
    #:     0.30 -> R@3 0.96  MRR 0.964  FPR 0.41   (recall parity criterion met)
    #:     0.35 -> R@3 0.82  MRR 0.821  FPR 0.18   (chosen)
    #:     0.45 -> R@3 0.61  MRR 0.607  FPR 0.00
    #:
    #: 0.35 is chosen on asymmetric cost: a missed answer makes the assistant say it does
    #: not know, which is safe and recoverable, while a false positive answers a question
    #: the company never addressed. It still ranks better than the deployed baseline
    #: (MRR 0.821 vs 0.804) and fabricates less than half as often. Cross-tenant leak rate
    #: is 0.00 at every threshold, since tenant filtering precedes ranking entirely.
    #: Tunable without a deploy; re-measure once real documents are indexed.
    retrieval_min_similarity: float = 0.35
    #: Most chunks any single answer may rest on.
    retrieval_max_chunks: int = 5
    #: Caps what one upload can cost us to parse, embed and store.
    knowledge_max_upload_bytes: int = 20 * 1024 * 1024
    knowledge_max_documents_per_company: int = 200

    #: Which retriever answers the customer's policy questions.
    #:
    #: ``keyword`` is Step 3A and is what production serves. ``hybrid`` is the cutover
    #: switch: it is implemented and tested, and falls back to ``keyword`` if embeddings
    #: or vector search fail, but it must not be turned on until a shadow run over real
    #: traffic says it should be. The 0.35 threshold below was measured on a 45-query
    #: synthetic fixture, which is enough to reject a bad threshold and not enough to
    #: choose a final one.
    knowledge_retrieval_mode: Literal["keyword", "hybrid"] = "keyword"
    #: Run the other retriever alongside the served one and log how they disagreed.
    #: Costs one embedding call per customer question, so it is opt-in.
    knowledge_shadow_enabled: bool = False
    #: Log the customer's question text alongside a shadow comparison.
    #:
    #: Off by default and deliberately its own switch: everything else a comparison
    #: records is counts and a hash, and this is the one field that is personal data.
    #: Turning it on makes disagreements far easier to diagnose and puts customer text
    #: in the application logs — a tradeoff for an operator to make knowingly.
    knowledge_shadow_log_queries: bool = False

    # --- Payments ---
    #: "fake" (offline, used by every test and by local development) or "stripe".
    payment_provider: str = "fake"
    stripe_secret_key: str = ""
    #: Signing secret for the webhook endpoint. Without it a signed event cannot be
    #: verified, and an unverified event is never acted on.
    stripe_webhook_secret: str = ""
    #: Where Stripe returns the customer. ``{token}`` is substituted per quote.
    payment_success_path: str = "/quote/{token}/paid"
    payment_cancel_path: str = "/quote/{token}"

    # --- LLM / agent ---
    openai_api_key: str = ""
    agent_model: str = "gpt-5-mini"
    agent_max_output_tokens: int = 4096
    # GPT-5 family supports minimal | low | medium | high. "low" keeps latency and
    # cost down while still reasoning enough to pick the right tool reliably;
    # "minimal" trades tool-selection reliability for speed.
    agent_reasoning_effort: str = "low"
    agent_timeout_seconds: float = 30.0

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
