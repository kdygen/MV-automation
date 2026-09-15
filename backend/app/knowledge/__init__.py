"""Semantic company-knowledge retrieval: chunking, fusion, and gating.

A pure package like :mod:`app.pricing` — no database, no HTTP, no model calls. Chunking
is deterministic arithmetic over text, fusion is rank arithmetic, and gating is a
threshold. Keeping them here means the parts most likely to be wrong can be tested
without fixtures and behave identically whichever database is underneath.
"""

from app.knowledge.chunking import Chunk, chunk_text, estimate_tokens, normalize
from app.knowledge.fusion import (
    RRF_K,
    Candidate,
    deduplicate,
    gate,
    merge_arms,
    reciprocal_rank_fusion,
)

__all__ = [
    "RRF_K",
    "Candidate",
    "Chunk",
    "chunk_text",
    "deduplicate",
    "estimate_tokens",
    "gate",
    "merge_arms",
    "normalize",
    "reciprocal_rank_fusion",
]
