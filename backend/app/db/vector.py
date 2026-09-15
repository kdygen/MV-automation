"""Portable embedding column: pgvector on PostgreSQL, JSON array on SQLite.

Same trick as :data:`~app.db.types.JSONType`, for the same reason. One set of models has
to be valid against production Postgres *and* the in-memory SQLite the test suite runs
on, and adding a Postgres container to every test run would cost minutes on a suite that
currently finishes in eleven seconds with no network at all.

What differs between the two is only **candidate generation**: PostgreSQL orders by the
``<=>`` cosine operator against an HNSW index, SQLite computes cosine in Python over the
tenant's rows. Ranking, fusion and gating are shared pure code, so the part most likely
to contain a bug is exercised identically on both. An opt-in parity test pins the two
candidate paths to the same ordering.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pgvector.sqlalchemy import Vector
from sqlalchemy.types import JSON

#: text-embedding-3-small. 1536 also sits under pgvector's 2000-dimension ceiling for
#: HNSW on the standard ``vector`` type, which 3-large's 3072 would not.
EMBEDDING_DIMENSIONS = 1536

#: The model that produced a stored vector. Embeddings from different models are not
#: comparable, so retrieval always filters on this alongside the tenant.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

EmbeddingVector = Vector(EMBEDDING_DIMENSIONS).with_variant(JSON(), "sqlite")


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity in ``[-1, 1]``, used by the SQLite candidate path.

    PostgreSQL computes this with ``1 - (a <=> b)``; this is the same quantity so the
    two paths produce comparable scores and a single threshold governs both.
    """
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimensions")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)
