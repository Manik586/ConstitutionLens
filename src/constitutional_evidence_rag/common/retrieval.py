"""Retrieval result model — the Phase 3 output that Phase 4 reranking consumes
(SRS FR-05 - FR-07, NFR-03, NFR-06; docs/DECISIONS.md D19).

A RetrievedChunk wraps the complete Phase 2 `Chunk`, unmodified, so every
provenance field (corpus, document id and version, source URL, pages, article,
heading, opinion author, …) survives retrieval exactly as chunking produced it.
Alongside it are the ranks and scores from each method that found the chunk, so
a reranker or an evaluation run can see why a chunk was retrieved (NFR-06).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from constitutional_evidence_rag.common.chunks import Chunk


class RetrievalMethod(str, Enum):
    BM25 = "bm25"  # Experiment A/B component (FR-06)
    DENSE = "dense"  # Experiment A (FR-05)
    HYBRID = "hybrid"  # Experiment B: BM25 + dense fused by RRF (FR-07)


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk: Chunk
    method: RetrievalMethod
    rank: int = Field(ge=1)  # 1-based rank in this result list
    score: float  # the method's own score: BM25, cosine similarity, or RRF

    # Per-component evidence (None when that component did not return the chunk).
    bm25_rank: int | None = Field(default=None, ge=1)
    bm25_score: float | None = None
    dense_rank: int | None = Field(default=None, ge=1)
    dense_score: float | None = None

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id
