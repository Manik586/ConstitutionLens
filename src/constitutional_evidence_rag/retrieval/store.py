"""Shared index plumbing for Phase 3 (D17 - D19).

Every index lives under <indexes_root>/<corpus_id>/<kind>/ (FR-05a, D15
corpus isolation) and stores `chunk_order.npy`: the chunk IDs in index-row
order. Row i of the BM25 statistics, of embeddings.npy and of the FAISS index
is chunk_order[i], which is how any index position maps back to a chunk.

Indexes also record a fingerprint of the chunk list they were built from, so
a retriever refuses to serve results from an index built for different chunks
(e.g. after re-chunking) instead of silently returning wrong provenance.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from constitutional_evidence_rag.ingestion.pipeline import corpus_dir

BM25_KIND = "bm25"
DENSE_KIND = "dense"
CHUNK_ORDER_FILENAME = "chunk_order.npy"
META_FILENAME = "meta.json"


class RetrievalIndexError(Exception):
    """Base class for index problems."""


class IndexNotFoundError(RetrievalIndexError):
    """The requested index has not been built for this corpus."""


class IndexStaleError(RetrievalIndexError):
    """The index was built from a different chunk list than chunks.jsonl now holds."""


class IndexMismatchError(RetrievalIndexError):
    """The index was built with a different embedding model / dimension than requested."""


class QueryError(ValueError):
    """The query or a retrieval parameter is invalid."""


def index_dir(indexes_root: Path, corpus_id: str, kind: str) -> Path:
    """<indexes_root>/<corpus_id>/<kind>; validates corpus_id (it is a path component)."""
    return corpus_dir(indexes_root, corpus_id) / kind


def chunk_fingerprint(chunk_ids: Sequence[str]) -> str:
    """Identifies an ordered chunk list. Chunk IDs embed a hash of their text (D16),
    so any change in chunk text or order changes the fingerprint."""
    return hashlib.sha256("\n".join(chunk_ids).encode("utf-8")).hexdigest()


def save_array(directory: Path, name: str, array: np.ndarray) -> None:
    np.save(directory / name, array, allow_pickle=False)


def load_array(directory: Path, name: str) -> np.ndarray:
    return np.load(directory / name, allow_pickle=False)


def save_chunk_order(directory: Path, chunk_ids: Sequence[str]) -> None:
    save_array(directory, CHUNK_ORDER_FILENAME, np.array(list(chunk_ids), dtype=str))


def load_chunk_order(directory: Path) -> list[str]:
    return [str(x) for x in load_array(directory, CHUNK_ORDER_FILENAME).tolist()]


def write_meta(directory: Path, meta: dict[str, Any]) -> None:
    """Sorted keys, no timestamps: rebuilding from the same inputs gives identical bytes."""
    (directory / META_FILENAME).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_meta(directory: Path) -> dict[str, Any]:
    path = directory / META_FILENAME
    if not path.exists():
        raise IndexNotFoundError(f"No index at {directory} (missing {META_FILENAME}) — run scripts/build_indexes.py")
    return json.loads(path.read_text(encoding="utf-8"))


class StagedIndexDir:
    """Build an index in a sibling temp directory, then swap it into place, so a
    failed or interrupted build never leaves a half-written index behind."""

    def __init__(self, target: Path):
        self.target = target

    def __enter__(self) -> Path:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix=f".{self.target.name}.", dir=self.target.parent))
        return self.tmp

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)
            return
        os.chmod(self.tmp, 0o755)  # mkdtemp creates 0700
        if self.target.exists():
            shutil.rmtree(self.target)
        os.replace(self.tmp, self.target)


def validate_query(query: object) -> str:
    if not isinstance(query, str):
        raise QueryError(f"query must be a string, got {type(query).__name__}")
    stripped = query.strip()
    if not stripped:
        raise QueryError("query is empty")
    return stripped


def validate_top_n(top_n: object) -> int:
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise QueryError(f"top_n must be a positive integer, got {top_n!r}")
    return top_n
