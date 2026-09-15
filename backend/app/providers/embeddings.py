"""Embedding provider: one thin adapter around the vendor SDK, plus an offline fake.

Same shape as the distance, email, LLM and payment providers, and for the same reasons —
the whole test suite runs with no API key and no network, and swapping providers never
touches the retrieval code.

Two properties matter beyond "turns text into vectors":

* **Batching.** Indexing a 40-page policy is ~100 chunks. One request, not a hundred.
* **Partial failure is visible.** A batch that half-succeeds must say so rather than
  silently returning fewer vectors than it was given, because a caller that zips inputs
  to outputs would otherwise attach the wrong vector to the wrong chunk.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.vector import DEFAULT_EMBEDDING_MODEL, EMBEDDING_DIMENSIONS

logger = get_logger(__name__)

#: OpenAI accepts up to 2048 inputs per request; 128 keeps individual requests small
#: enough to retry cheaply while still collapsing a large document into a few calls.
BATCH_SIZE = 128

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0


class EmbeddingError(RuntimeError):
    """Embedding could not be produced. Callers keep the previous index and report."""


class EmbeddingConfigurationError(RuntimeError):
    """A configured embedding provider is unknown or missing credentials."""


@dataclass(frozen=True)
class EmbeddingResult:
    """Vectors in the same order as the inputs, and the model that produced them."""

    vectors: list[list[float]]
    model: str


class EmbeddingProvider(Protocol):
    """Everything indexing and retrieval need from an embedding model."""

    @property
    def model(self) -> str: ...

    def embed(self, texts: list[str]) -> EmbeddingResult: ...


class FakeEmbeddingProvider:
    """Deterministic offline embeddings for tests and local development.

    Vectors are derived from token hashes, so semantically similar strings that *share
    words* land near each other while unrelated strings do not. That is enough to test
    ranking, fusion, thresholds and tenant isolation honestly — but it is a bag-of-words
    geometry, not a language model, so it cannot demonstrate paraphrase understanding.
    Any evaluation run against this provider must say so.
    """

    model = "fake-embedding-v1"

    def __init__(self, *, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(vectors=[self._vector(t) for t in texts], model=self.model)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode()).digest()
            # Two buckets per token with opposite signs keeps vectors roughly centred,
            # so unrelated texts are near-orthogonal rather than uniformly positive.
            for offset in range(2):
                raw = int.from_bytes(digest[offset * 4 : offset * 4 + 4], "big")
                index = raw % self._dimensions
                vector[index] += 1.0 if offset == 0 else -0.5
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector


def _tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", text.lower())


class OpenAIEmbeddingProvider:
    """The only module that calls the embeddings API."""

    def __init__(
        self, *, api_key: str, model: str = DEFAULT_EMBEDDING_MODEL, timeout: float = 30.0
    ) -> None:
        from openai import OpenAI

        # Typed loosely: this adapter is the boundary where the vendor's shapes stop,
        # exactly as in the LLM and payment adapters.
        self._client: Any = OpenAI(api_key=api_key, timeout=timeout)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self._model)

        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            vectors.extend(self._embed_batch(batch))
        return EmbeddingResult(vectors=vectors, model=self._model)

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._client.embeddings.create(model=self._model, input=batch)
                # Order is not promised by index position alone, so sort by the index the
                # API returns; zipping blindly is how vectors end up on the wrong chunk.
                items = sorted(response.data, key=lambda item: item.index)
                if len(items) != len(batch):
                    raise EmbeddingError(
                        f"expected {len(batch)} embeddings, received {len(items)}"
                    )
                return [list(item.embedding) for item in items]
            except Exception as exc:
                last_error = exc
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        logger.warning("Embedding request failed after %d attempts", MAX_ATTEMPTS)
        raise EmbeddingError(str(last_error)) from last_error


#: Built providers, keyed by what actually determines their behaviour. An OpenAI client
#: owns an HTTP connection pool, so constructing one per knowledge search would leak
#: sockets and pay TLS setup on every customer turn. Credentials are read from the
#: cached ``Settings``, so a key change needs a restart either way.
_BUILT: dict[tuple[str, str], FakeEmbeddingProvider | OpenAIEmbeddingProvider] = {}


def resolve_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    """The configured provider, or ``None`` when it is not usable.

    Returning ``None`` rather than raising is the single decision that keeps a missing
    API key from becoming an outage: indexing already treats an absent vector as
    "lexically retrievable only", and retrieval already falls back to the keyword arm.
    A misconfiguration therefore degrades search quality and is logged for an operator,
    instead of failing an owner's upload or a customer's question.
    """
    try:
        key = ((settings.embedding_provider or "fake").lower(), settings.embedding_model)
        if key not in _BUILT:
            _BUILT[key] = get_embedding_provider(settings)
        return _BUILT[key]
    except EmbeddingConfigurationError as exc:
        logger.warning("Embeddings unavailable, falling back to keyword search: %s", exc)
        return None


def get_embedding_provider(settings: Settings) -> FakeEmbeddingProvider | OpenAIEmbeddingProvider:
    """Return the provider selected by ``settings.embedding_provider``."""
    name = (settings.embedding_provider or "fake").lower()
    if name == "fake":
        return FakeEmbeddingProvider()
    if name == "openai":
        if not settings.openai_api_key:
            raise EmbeddingConfigurationError(
                "EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY to be set"
            )
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key, model=settings.embedding_model
        )
    raise EmbeddingConfigurationError(
        f"Unknown embedding provider: {settings.embedding_provider!r}"
    )
