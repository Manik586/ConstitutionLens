"""Retriever: BM25, dense and hybrid retrieval over one corpus (SRS FR-05 - FR-07,
FR-05a; docs/DECISIONS.md D19).

    Retriever.load(processed_root, indexes_root, corpus_id, settings)
        .bm25(query)    Experiment A/B component
        .dense(query)   Experiment A
        .hybrid(query)  Experiment B — the candidate list Phase 4 reranks (FR-08)

Each call returns list[RetrievedChunk]: the full Phase 2 Chunk (all provenance)
plus per-method ranks/scores. corpus_id is a *scope* (which index to open),
never a ranking input (FR-SA-05). On load, every index is checked against the
corpus's current chunks.jsonl; a stale index is refused rather than allowed to
return chunks with wrong provenance.
"""
from __future__ import annotations

from pathlib import Path

from constitutional_evidence_rag.chunking.legal_chunker import CHUNKS_FILENAME, load_chunks
from constitutional_evidence_rag.common.chunks import Chunk
from constitutional_evidence_rag.common.config import Settings
from constitutional_evidence_rag.common.logging import get_logger
from constitutional_evidence_rag.common.retrieval import RetrievalMethod, RetrievedChunk
from constitutional_evidence_rag.ingestion.pipeline import corpus_dir
from constitutional_evidence_rag.retrieval.bm25 import BM25Index
from constitutional_evidence_rag.retrieval.dense import DenseIndex, Embedder, make_embedder
from constitutional_evidence_rag.retrieval.rrf import reciprocal_rank_fusion
from constitutional_evidence_rag.retrieval.store import (
    BM25_KIND,
    DENSE_KIND,
    IndexNotFoundError,
    IndexStaleError,
    chunk_fingerprint,
    index_dir,
    validate_query,
    validate_top_n,
)

logger = get_logger(__name__)


class Retriever:
    def __init__(self, chunks: list[Chunk], settings: Settings, *, bm25: BM25Index | None = None,
                 dense: DenseIndex | None = None, embedder: Embedder | None = None):
        self.chunks = chunks
        self.settings = settings
        self._bm25 = bm25
        self._dense = dense
        self._embedder = embedder
        if dense is not None and embedder is not None:
            dense.check_embedder(embedder)

    @classmethod
    def load(cls, *, processed_root: Path, indexes_root: Path, corpus_id: str, settings: Settings,
             embedder: Embedder | None = None) -> Retriever:
        """Open whichever indexes exist for the corpus. The embedding model is loaded
        lazily on the first dense query unless an embedder is passed in."""
        chunks_path = corpus_dir(processed_root, corpus_id) / CHUNKS_FILENAME
        if not chunks_path.exists():
            raise IndexNotFoundError(f"No chunks at {chunks_path} — run scripts/chunk_corpus.py first")
        chunks = load_chunks(chunks_path)
        expected = [c.chunk_id for c in chunks]

        def checked(kind: str, index):
            if index.chunk_ids != expected:
                raise IndexStaleError(
                    f"{kind} index for {corpus_id!r} was built from different chunks "
                    f"(fingerprint {chunk_fingerprint(index.chunk_ids)[:12]} != {chunk_fingerprint(expected)[:12]}) "
                    "— rebuild with scripts/build_indexes.py")
            return index

        bm25 = dense = None
        if (index_dir(indexes_root, corpus_id, BM25_KIND) / "meta.json").exists():
            bm25 = checked("BM25", BM25Index.load(index_dir(indexes_root, corpus_id, BM25_KIND)))
        if (index_dir(indexes_root, corpus_id, DENSE_KIND) / "meta.json").exists():
            dense = checked("dense", DenseIndex.load(index_dir(indexes_root, corpus_id, DENSE_KIND)))
        if bm25 is None and dense is None:
            raise IndexNotFoundError(f"No indexes for {corpus_id!r} under {indexes_root} — run scripts/build_indexes.py")
        return cls(chunks, settings, bm25=bm25, dense=dense, embedder=embedder)

    # ---------------------------------------------------------------- components

    def _bm25_hits(self, query: str, top_n: int) -> list[tuple[int, float]]:
        if self._bm25 is None:
            raise IndexNotFoundError("BM25 index not built for this corpus")
        r = self.settings.retrieval
        return self._bm25.search(query, top_n, k1=r.bm25_k1, b=r.bm25_b)

    def _dense_hits(self, query: str, top_n: int) -> list[tuple[int, float]]:
        if self._dense is None:
            raise IndexNotFoundError("dense index not built for this corpus")
        if self._embedder is None:
            self._embedder = make_embedder(self.settings.embedding)
            self._dense.check_embedder(self._embedder)
        return self._dense.search(self._embedder.embed_query(query), top_n)

    def _result(self, row: int, method: RetrievalMethod, rank: int, score: float, **extra) -> RetrievedChunk:
        return RetrievedChunk(chunk=self.chunks[row], method=method, rank=rank, score=score, **extra)

    # ---------------------------------------------------------------- public API

    def bm25(self, query: str, top_n: int | None = None) -> list[RetrievedChunk]:
        query = validate_query(query)
        hits = self._bm25_hits(query, validate_top_n(self.settings.retrieval.top_n_bm25 if top_n is None else top_n))
        return [self._result(row, RetrievalMethod.BM25, i, s, bm25_rank=i, bm25_score=s)
                for i, (row, s) in enumerate(hits, start=1)]

    def dense(self, query: str, top_n: int | None = None) -> list[RetrievedChunk]:
        query = validate_query(query)
        hits = self._dense_hits(query, validate_top_n(self.settings.retrieval.top_n_dense if top_n is None else top_n))
        return [self._result(row, RetrievalMethod.DENSE, i, s, dense_rank=i, dense_score=s)
                for i, (row, s) in enumerate(hits, start=1)]

    def hybrid(self, query: str, top_n: int | None = None) -> list[RetrievedChunk]:
        """BM25 top-N and dense top-N fused by weighted RRF (FR-07). A query with no
        BM25-indexable terms still works: it fuses the dense list alone."""
        query = validate_query(query)
        r = self.settings.retrieval
        top_n = validate_top_n(r.top_n_hybrid if top_n is None else top_n)
        bm25_hits = self._bm25_hits(query, r.top_n_bm25)
        dense_hits = self._dense_hits(query, r.top_n_dense)
        bm25_by_row = {row: (rank, s) for rank, (row, s) in enumerate(bm25_hits, start=1)}
        dense_by_row = {row: (rank, s) for rank, (row, s) in enumerate(dense_hits, start=1)}
        fused = reciprocal_rank_fusion(
            {"bm25": [row for row, _ in bm25_hits], "dense": [row for row, _ in dense_hits]},
            k=r.rrf_k, weights={"bm25": r.weight_bm25, "dense": r.weight_dense},
        )[:top_n]
        results = []
        for rank, (row, score) in enumerate(fused, start=1):
            b, d = bm25_by_row.get(row), dense_by_row.get(row)
            results.append(self._result(
                row, RetrievalMethod.HYBRID, rank, score,
                bm25_rank=b[0] if b else None, bm25_score=b[1] if b else None,
                dense_rank=d[0] if d else None, dense_score=d[1] if d else None,
            ))
        return results
