"""Dense retrieval: embeddings + FAISS (SRS FR-05, §39, NFR-05; docs/DECISIONS.md D17).

Storage (<indexes_root>/<corpus_id>/dense/):
    embeddings.npy   float32 [n_chunks, dim], L2-normalized, row i = chunk_order[i]
    chunk_order.npy  chunk IDs in row order — FAISS position i  <->  chunk_order[i]
    index.faiss      IndexFlatIP over the same rows (exact cosine similarity)
    meta.json        model name, dimension, chunk fingerprint

Embeddings are cached: a rebuild reuses the stored vector of every chunk whose
chunk_id is unchanged (chunk IDs embed a hash of their text, D16), provided the
model name and dimension are unchanged, and computes only new chunks. FAISS is
cheap to rebuild from embeddings.npy, so it is always rebuilt.

The model is behind a small `Embedder` interface (NFR-05: swappable), so tests
and other backends need not download weights. Only chunk text is embedded;
no authority/section-type metadata enters the vectors (FR-SA-05).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from constitutional_evidence_rag.common.chunks import Chunk
from constitutional_evidence_rag.common.config import EmbeddingSettings
from constitutional_evidence_rag.common.logging import get_logger
from constitutional_evidence_rag.retrieval.store import (
    IndexMismatchError,
    RetrievalIndexError,
    StagedIndexDir,
    chunk_fingerprint,
    load_array,
    load_chunk_order,
    read_meta,
    save_array,
    save_chunk_order,
    validate_top_n,
    write_meta,
)

logger = get_logger(__name__)

FORMAT_VERSION = 1
EMBEDDINGS_FILENAME = "embeddings.npy"
FAISS_FILENAME = "index.faiss"


class Embedder(Protocol):
    """What the dense index needs from an embedding model."""

    model_name: str

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    """BGE (or any sentence-transformers model). The library is imported lazily, so
    BM25-only use and the test suite never load torch or download weights."""

    def __init__(self, settings: EmbeddingSettings):
        from sentence_transformers import SentenceTransformer  # heavy import, deliberately late

        self.model_name = settings.model_name
        self._settings = settings
        self._model = SentenceTransformer(settings.model_name, device=settings.device)

    @property
    def dimension(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._model.encode(
            list(texts), batch_size=self._settings.batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=len(texts) > self._settings.batch_size,
        ).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._model.encode(
            [self._settings.query_instruction + text], normalize_embeddings=True, convert_to_numpy=True,
        )[0].astype(np.float32)


def make_embedder(settings: EmbeddingSettings) -> Embedder:
    return SentenceTransformerEmbedder(settings)


def _normalized(vectors: np.ndarray, dimension: int) -> np.ndarray:
    """Defensive: guarantee float32, the right shape, finite values and unit length,
    whatever the embedder returns, so inner product == cosine similarity."""
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[1] != dimension:
        raise RetrievalIndexError(f"embedder returned shape {vectors.shape}, expected (n, {dimension})")
    if not np.isfinite(vectors).all():
        raise RetrievalIndexError("embedder returned non-finite values")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.ascontiguousarray(vectors / np.where(norms == 0, 1.0, norms), dtype=np.float32)


@dataclass(frozen=True)
class DenseBuildReport:
    n_chunks: int
    dimension: int
    model_name: str
    reused: int
    computed: int


def build_dense_index(chunks: Sequence[Chunk], directory: Path, embedder: Embedder) -> DenseBuildReport:
    """Embed every chunk (reusing cached vectors where valid) and build the FAISS index."""
    import faiss

    if not chunks:
        raise RetrievalIndexError("cannot build a dense index from zero chunks")
    dimension = embedder.dimension
    cached = _load_cached_vectors(directory, embedder.model_name, dimension)

    chunk_ids = [c.chunk_id for c in chunks]
    missing = [i for i, cid in enumerate(chunk_ids) if cid not in cached]
    vectors = np.zeros((len(chunks), dimension), dtype=np.float32)
    for i, cid in enumerate(chunk_ids):
        if cid in cached:
            vectors[i] = cached[cid]
    if missing:
        logger.info("Embedding %d of %d chunks with %s", len(missing), len(chunks), embedder.model_name)
        vectors[missing] = _normalized(embedder.embed_documents([chunks[i].text for i in missing]), dimension)

    index = faiss.IndexFlatIP(dimension)
    index.add(vectors)
    with StagedIndexDir(directory) as tmp:
        save_array(tmp, EMBEDDINGS_FILENAME, vectors)
        save_chunk_order(tmp, chunk_ids)
        faiss.write_index(index, str(tmp / FAISS_FILENAME))
        write_meta(tmp, {
            "kind": "dense",
            "format_version": FORMAT_VERSION,
            "model_name": embedder.model_name,
            "dimension": dimension,
            "metric": "inner_product_on_l2_normalized (cosine)",
            "faiss_index": "IndexFlatIP",
            "n_chunks": len(chunk_ids),
            "chunks_fingerprint": chunk_fingerprint(chunk_ids),
        })
    return DenseBuildReport(len(chunks), dimension, embedder.model_name, len(chunks) - len(missing), len(missing))


def _load_cached_vectors(directory: Path, model_name: str, dimension: int) -> dict[str, np.ndarray]:
    try:
        meta = read_meta(directory)
        if meta.get("model_name") != model_name or meta.get("dimension") != dimension:
            logger.info("Embedding model changed (%s -> %s): recomputing all vectors", meta.get("model_name"), model_name)
            return {}
        vectors = load_array(directory, EMBEDDINGS_FILENAME)
        ids = load_chunk_order(directory)
    except (RetrievalIndexError, OSError, ValueError):
        return {}
    if vectors.shape != (len(ids), dimension):
        return {}
    return {cid: vectors[i] for i, cid in enumerate(ids)}


class DenseIndex:
    """A loaded FAISS index plus the row -> chunk_id mapping."""

    def __init__(self, index, chunk_ids: list[str], meta: dict):
        self.index, self.chunk_ids, self.meta = index, chunk_ids, meta

    @property
    def model_name(self) -> str:
        return self.meta["model_name"]

    @property
    def dimension(self) -> int:
        return int(self.meta["dimension"])

    @classmethod
    def load(cls, directory: Path) -> DenseIndex:
        import faiss

        meta = read_meta(directory)
        if meta.get("kind") != "dense":
            raise RetrievalIndexError(f"{directory} does not hold a dense index")
        index = faiss.read_index(str(directory / FAISS_FILENAME))
        chunk_ids = load_chunk_order(directory)
        if index.ntotal != len(chunk_ids) or index.d != meta["dimension"]:
            raise RetrievalIndexError(
                f"dense index at {directory} is inconsistent: {index.ntotal} vectors / dim {index.d}, "
                f"{len(chunk_ids)} chunk ids / dim {meta['dimension']} — rebuild it")
        return cls(index, chunk_ids, meta)

    def check_embedder(self, embedder: Embedder) -> None:
        if embedder.model_name != self.model_name or embedder.dimension != self.dimension:
            raise IndexMismatchError(
                f"dense index was built with {self.model_name} (dim {self.dimension}); query embedder is "
                f"{embedder.model_name} (dim {embedder.dimension}) — rebuild the index or change embedding.model_name")

    def search(self, query_vector: np.ndarray, top_n: int) -> list[tuple[int, float]]:
        """(row, cosine) pairs, best first; ties broken by row."""
        top_n = validate_top_n(top_n)
        q = _normalized(np.asarray(query_vector, dtype=np.float32).reshape(1, -1), self.dimension)
        scores, rows = self.index.search(q, min(top_n, len(self.chunk_ids)))
        hits = [(int(r), float(s)) for r, s in zip(rows[0], scores[0]) if r >= 0]
        return sorted(hits, key=lambda h: (-h[1], h[0]))
