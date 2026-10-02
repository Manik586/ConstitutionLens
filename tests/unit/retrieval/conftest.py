"""Shared Phase 3 fixtures: a written chunks.jsonl plus built indexes."""
from __future__ import annotations

import pytest

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.retrieval.bm25 import BM25Index
from constitutional_evidence_rag.retrieval.dense import build_dense_index
from constitutional_evidence_rag.retrieval.store import BM25_KIND, DENSE_KIND, index_dir

from retrieval_fixtures import HashingEmbedder, make_chunks, make_settings, write_chunks


@pytest.fixture
def chunks():
    return make_chunks()


@pytest.fixture
def indexed(tmp_path, chunks):
    """chunks.jsonl + BM25 + dense indexes for the curated corpus under tmp_path."""
    settings = make_settings(tmp_path)
    write_chunks(settings.paths.data_processed_dir, chunks)
    embedder = HashingEmbedder()
    BM25Index.build(chunks).save(index_dir(settings.paths.indexes_dir, CURATED_CORPUS_ID, BM25_KIND))
    build_dense_index(chunks, index_dir(settings.paths.indexes_dir, CURATED_CORPUS_ID, DENSE_KIND), embedder)
    return {"settings": settings, "chunks": chunks, "embedder": embedder, "tmp": tmp_path}
