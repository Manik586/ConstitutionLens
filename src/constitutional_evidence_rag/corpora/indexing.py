"""All-or-nothing index build for one corpus (Phase 7; D28).

BM25 and dense indexes are built into a staging directory beside indexes/<corpus_id>/ and
swapped in together only when both succeed, so a query can never see a half-built or
mixed (new BM25 + old dense) index, and a failed build leaves the previous indexes intact.
The existing dense index is copied into staging first so unchanged chunks keep their cached
embeddings (D17). Uses the unchanged Phase 3 builders.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from constitutional_evidence_rag.common.chunks import Chunk
from constitutional_evidence_rag.retrieval.bm25 import BM25Index
from constitutional_evidence_rag.retrieval.dense import DenseBuildReport, Embedder, build_dense_index
from constitutional_evidence_rag.retrieval.store import BM25_KIND, DENSE_KIND


def build_indexes_atomically(chunks: list[Chunk], corpus_index_dir: Path, embedder: Embedder) -> tuple[int, DenseBuildReport]:
    """Build both indexes for one corpus; returns (BM25 vocabulary size, dense report)."""
    parent, name = corpus_index_dir.parent, corpus_index_dir.name
    parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    staging, backup = parent / f".{name}.staging-{token}", parent / f".{name}.previous-{token}"
    try:
        staging.mkdir()
        if (corpus_index_dir / DENSE_KIND).exists():
            shutil.copytree(corpus_index_dir / DENSE_KIND, staging / DENSE_KIND)  # embedding cache
        bm25 = BM25Index.build(chunks)
        bm25.save(staging / BM25_KIND)
        report = build_dense_index(chunks, staging / DENSE_KIND, embedder)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    had_previous = corpus_index_dir.exists()
    if had_previous:
        os.replace(corpus_index_dir, backup)
    try:
        os.replace(staging, corpus_index_dir)
    except BaseException:
        if had_previous:
            os.replace(backup, corpus_index_dir)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    return bm25.vocab_size, report
