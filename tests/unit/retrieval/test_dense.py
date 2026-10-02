import hashlib

import numpy as np
import pytest

from constitutional_evidence_rag.retrieval.dense import DenseIndex, build_dense_index
from constitutional_evidence_rag.retrieval.store import IndexMismatchError, QueryError, RetrievalIndexError, load_chunk_order, read_meta

from retrieval_fixtures import HashingEmbedder, make_chunks


def test_build_writes_embeddings_mapping_and_faiss(tmp_path, chunks):
    report = build_dense_index(chunks, tmp_path / "dense", HashingEmbedder())
    directory = tmp_path / "dense"

    assert sorted(p.name for p in directory.iterdir()) == ["chunk_order.npy", "embeddings.npy", "index.faiss", "meta.json"]
    vectors = np.load(directory / "embeddings.npy", allow_pickle=False)
    assert vectors.shape == (len(chunks), 64) and vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    assert load_chunk_order(directory) == [c.chunk_id for c in chunks]
    meta = read_meta(directory)
    assert (meta["model_name"], meta["dimension"], meta["n_chunks"]) == ("test/hashing-64", 64, len(chunks))
    assert (report.computed, report.reused) == (len(chunks), 0)


def test_every_faiss_position_maps_back_to_its_chunk(tmp_path, chunks):
    embedder = HashingEmbedder()
    build_dense_index(chunks, tmp_path / "dense", embedder)
    index = DenseIndex.load(tmp_path / "dense")

    assert index.index.ntotal == len(index.chunk_ids) == len(chunks)
    for row, chunk in enumerate(chunks):
        top_row, score = index.search(embedder.embed_query(chunk.text), 1)[0]
        assert index.chunk_ids[top_row] == chunk.chunk_id and top_row == row
        assert score == pytest.approx(1.0, abs=1e-5)


def test_unchanged_chunks_reuse_cached_embeddings(tmp_path, chunks):
    build_dense_index(chunks, tmp_path / "dense", HashingEmbedder())
    embedder = HashingEmbedder()
    report = build_dense_index(chunks, tmp_path / "dense", embedder)

    assert (report.reused, report.computed, embedder.document_batches) == (len(chunks), 0, [])


def test_only_new_chunks_are_embedded(tmp_path, chunks):
    build_dense_index(chunks[:-1], tmp_path / "dense", HashingEmbedder())
    embedder = HashingEmbedder()
    report = build_dense_index(chunks, tmp_path / "dense", embedder)

    assert (report.reused, report.computed, embedder.document_batches) == (len(chunks) - 1, 1, [1])


def test_model_change_recomputes_everything(tmp_path, chunks):
    build_dense_index(chunks, tmp_path / "dense", HashingEmbedder())
    embedder = HashingEmbedder(model_name="test/other-model")
    report = build_dense_index(chunks, tmp_path / "dense", embedder)

    assert report.computed == len(chunks) and embedder.document_batches == [len(chunks)]


def test_cached_rebuild_is_byte_identical(tmp_path, chunks):
    build_dense_index(chunks, tmp_path / "a", HashingEmbedder())
    build_dense_index(make_chunks(), tmp_path / "b", HashingEmbedder())
    build_dense_index(make_chunks(), tmp_path / "b", HashingEmbedder())  # second pass served from cache
    digest = lambda d: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}

    assert digest(tmp_path / "a") == digest(tmp_path / "b")


def test_query_with_a_different_model_is_refused(tmp_path, chunks):
    build_dense_index(chunks, tmp_path / "dense", HashingEmbedder())
    index = DenseIndex.load(tmp_path / "dense")

    with pytest.raises(IndexMismatchError, match="rebuild"):
        index.check_embedder(HashingEmbedder(model_name="BAAI/bge-small-en-v1.5"))
    with pytest.raises(IndexMismatchError):
        index.check_embedder(HashingEmbedder(dimension=32))


class _Raw(HashingEmbedder):
    def embed_documents(self, texts):
        return super().embed_documents(texts) * 7.0  # not unit length


class _WrongShape(HashingEmbedder):
    def embed_documents(self, texts):
        return np.ones((len(texts), 3), dtype=np.float32)


def test_embedder_output_is_validated_and_normalized(tmp_path, chunks):
    build_dense_index(chunks, tmp_path / "dense", _Raw())
    vectors = np.load(tmp_path / "dense" / "embeddings.npy")
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    with pytest.raises(RetrievalIndexError, match="shape"):
        build_dense_index(chunks, tmp_path / "bad", _WrongShape())
    assert not (tmp_path / "bad").exists()  # a failed build leaves nothing behind


def test_empty_chunks_and_bad_top_n(tmp_path, chunks):
    with pytest.raises(RetrievalIndexError):
        build_dense_index([], tmp_path / "dense", HashingEmbedder())
    build_dense_index(chunks, tmp_path / "dense", HashingEmbedder())
    with pytest.raises(QueryError):
        DenseIndex.load(tmp_path / "dense").search(np.ones(64, dtype=np.float32), 0)


def test_top_n_larger_than_corpus_returns_all(tmp_path, chunks):
    embedder = HashingEmbedder()
    build_dense_index(chunks, tmp_path / "dense", embedder)

    assert len(DenseIndex.load(tmp_path / "dense").search(embedder.embed_query("law"), 100)) == len(chunks)
