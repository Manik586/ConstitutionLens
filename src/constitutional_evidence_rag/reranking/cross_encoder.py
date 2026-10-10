"""SRS FR-08 cross-encoder reranking (Experiment C).

A cross-encoder reads the question and one passage together and outputs a relevance score,
which is more precise than comparing two independently computed embeddings. It is too slow
to score the whole corpus, so it re-orders a small candidate pool from hybrid retrieval.
The score is query relevance only: no source-type or authority weight (Invariant 1).
"""
from __future__ import annotations

from typing import Protocol, Sequence

from constitutional_evidence_rag.common.config import RerankingSettings
from constitutional_evidence_rag.common.retrieval import RetrievedChunk


class PairScorer(Protocol):
    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class SentenceTransformersCrossEncoder:
    """sentence-transformers CrossEncoder, imported lazily (no torch import for BM25-only use)."""

    def __init__(self, settings: RerankingSettings):
        from sentence_transformers import CrossEncoder  # heavy import, deliberately late

        self.model_name = settings.model_name
        self._settings = settings
        self._model = CrossEncoder(settings.model_name, max_length=settings.max_length, device=settings.device)

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        scores = self._model.predict([(query, p) for p in passages], batch_size=self._settings.batch_size,
                                     show_progress_bar=False, convert_to_numpy=True)
        return [float(s) for s in scores]


def make_cross_encoder(settings: RerankingSettings) -> PairScorer:
    return SentenceTransformersCrossEncoder(settings)


def rerank(query: str, candidates: list[RetrievedChunk], scorer: PairScorer) -> list[tuple[RetrievedChunk, float]]:
    """Candidates re-ordered by cross-encoder score (descending), each with its score.
    Ties keep the incoming (hybrid) order, so the result is deterministic."""
    scores = scorer.score(query, [c.chunk.text for c in candidates])
    order = sorted(range(len(candidates)), key=lambda i: (-scores[i], i))
    return [(candidates[i], scores[i]) for i in order]
