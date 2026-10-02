import pytest
from pydantic import ValidationError

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.common.retrieval import RetrievalMethod, RetrievedChunk
from constitutional_evidence_rag.retrieval.bm25 import BM25Index
from constitutional_evidence_rag.retrieval.dense import build_dense_index
from constitutional_evidence_rag.retrieval.hybrid import Retriever
from constitutional_evidence_rag.retrieval.store import (
    BM25_KIND, DENSE_KIND, IndexMismatchError, IndexNotFoundError, IndexStaleError, QueryError, index_dir,
)

from retrieval_fixtures import HashingEmbedder, make_chunks, make_settings, write_chunks


def load(indexed, settings=None, embedder=None, corpus_id=CURATED_CORPUS_ID):
    s = settings or indexed["settings"]
    return Retriever.load(processed_root=s.paths.data_processed_dir, indexes_root=s.paths.indexes_dir,
                          corpus_id=corpus_id, settings=s, embedder=embedder or indexed["embedder"])


QUERIES = ["Article 19(2) reasonable restrictions", "sexual harassment guidelines", "personal liberty", "amending power"]


# ------------------------------------------------------------------ valid ids and provenance


@pytest.mark.parametrize("mode", ["bm25", "dense", "hybrid"])
def test_results_are_valid_chunks_with_complete_provenance(indexed, mode):
    retriever = load(indexed)
    by_id = {c.chunk_id: c for c in indexed["chunks"]}
    for query in QUERIES:
        results = getattr(retriever, mode)(query)
        assert results, query
        assert [r.rank for r in results] == list(range(1, len(results) + 1))
        assert len({r.chunk_id for r in results}) == len(results)  # no duplicates
        for r in results:
            assert r.method is RetrievalMethod(mode)
            assert r.chunk == by_id[r.chunk_id]  # every provenance field identical to chunks.jsonl


def test_provenance_survives_json_round_trip(indexed):
    for r in load(indexed).hybrid("reasonable restrictions"):
        restored = RetrievedChunk.model_validate_json(r.model_dump_json())
        assert restored == r
        c = restored.chunk
        assert (c.corpus_id, c.document_version, c.source_url) == (CURATED_CORPUS_ID, 1, f"https://example.org/{c.document_id}")


def test_bm25_and_dense_scores_are_ordered(indexed):
    retriever = load(indexed)
    for mode, field in [("bm25", "bm25_score"), ("dense", "dense_score")]:
        results = getattr(retriever, mode)("reasonable restrictions")
        assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
        assert all(getattr(r, field) == r.score for r in results)


# ------------------------------------------------------------------ hybrid fusion


def test_hybrid_is_rrf_of_the_component_lists(indexed):
    retriever = load(indexed)
    r = indexed["settings"].retrieval
    query = "Article 19(2) reasonable restrictions"
    bm25 = {x.chunk_id: x.rank for x in retriever.bm25(query, r.top_n_bm25)}
    dense = {x.chunk_id: x.rank for x in retriever.dense(query, r.top_n_dense)}
    hybrid = retriever.hybrid(query)

    assert {x.chunk_id for x in hybrid} == set(bm25) | set(dense)  # union, de-duplicated
    for x in hybrid:
        expected = sum(w / (r.rrf_k + ranks[x.chunk_id])
                       for ranks, w in [(bm25, r.weight_bm25), (dense, r.weight_dense)] if x.chunk_id in ranks)
        assert x.score == pytest.approx(expected)
        assert (x.bm25_rank, x.dense_rank) == (bm25.get(x.chunk_id), dense.get(x.chunk_id))
    assert [x.score for x in hybrid] == sorted((x.score for x in hybrid), reverse=True)
    both = [x for x in hybrid if x.bm25_rank and x.dense_rank]
    assert both and hybrid[0] in both


def test_weights_are_configurable(indexed, tmp_path):
    settings = make_settings(indexed["tmp"], weight_dense=0.0)
    retriever = load(indexed, settings)
    query = "Article 19(2) reasonable restrictions"

    assert [x.chunk_id for x in retriever.hybrid(query)] == [x.chunk_id for x in retriever.bm25(query)]


def test_top_n_defaults_to_config_and_can_be_overridden(indexed):
    retriever = load(indexed, make_settings(indexed["tmp"], top_n_hybrid=3, top_n_dense=4))

    assert len(retriever.hybrid("law")) == 3
    assert len(retriever.dense("law")) == 4
    assert len(retriever.hybrid("law", top_n=2)) == 2


# ------------------------------------------------------------------ empty / invalid queries


@pytest.mark.parametrize("mode", ["bm25", "dense", "hybrid"])
@pytest.mark.parametrize("query", ["", "   ", "\n\t", None, 42])
def test_empty_or_non_string_query_is_rejected(indexed, mode, query):
    with pytest.raises(QueryError):
        getattr(load(indexed), mode)(query)


@pytest.mark.parametrize("top_n", [0, -3])
def test_invalid_top_n_is_rejected(indexed, top_n):
    with pytest.raises(QueryError):
        load(indexed).hybrid("law", top_n)


def test_query_without_indexable_terms(indexed):
    retriever = load(indexed)

    assert retriever.bm25("?!") == []
    hybrid = retriever.hybrid("?! liberty")  # still fused from whichever lists have hits
    assert hybrid and all(x.method is RetrievalMethod.HYBRID for x in hybrid)


# ------------------------------------------------------------------ safety checks


def test_missing_indexes_or_chunks(tmp_path):
    settings = make_settings(tmp_path)
    with pytest.raises(IndexNotFoundError, match="chunk_corpus"):
        Retriever.load(processed_root=settings.paths.data_processed_dir, indexes_root=settings.paths.indexes_dir,
                       corpus_id=CURATED_CORPUS_ID, settings=settings)
    write_chunks(settings.paths.data_processed_dir, make_chunks())
    with pytest.raises(IndexNotFoundError, match="build_indexes"):
        Retriever.load(processed_root=settings.paths.data_processed_dir, indexes_root=settings.paths.indexes_dir,
                       corpus_id=CURATED_CORPUS_ID, settings=settings)


def test_bm25_only_corpus_serves_bm25_but_not_dense(tmp_path, chunks):
    settings = make_settings(tmp_path)
    write_chunks(settings.paths.data_processed_dir, chunks)
    BM25Index.build(chunks).save(index_dir(settings.paths.indexes_dir, CURATED_CORPUS_ID, BM25_KIND))
    retriever = Retriever.load(processed_root=settings.paths.data_processed_dir, indexes_root=settings.paths.indexes_dir,
                               corpus_id=CURATED_CORPUS_ID, settings=settings)

    assert retriever.bm25("liberty")
    with pytest.raises(IndexNotFoundError):
        retriever.dense("liberty")


def test_stale_index_is_refused_after_rechunking(indexed):
    write_chunks(indexed["settings"].paths.data_processed_dir, make_chunks(text_suffix=" (revised)"))

    with pytest.raises(IndexStaleError, match="rebuild"):
        load(indexed)


def test_query_embedder_must_match_the_index_model(indexed):
    with pytest.raises(IndexMismatchError):
        load(indexed, embedder=HashingEmbedder(model_name="BAAI/bge-small-en-v1.5"))


def test_retrieval_is_deterministic(indexed):
    a, b = load(indexed), load(indexed)
    for mode in ("bm25", "dense", "hybrid"):
        for query in QUERIES:
            assert getattr(a, mode)(query) == getattr(b, mode)(query) == getattr(a, mode)(query)


def test_indexes_are_scoped_to_their_corpus(indexed):
    other = make_chunks(corpus_id="test-collection", text_suffix=" (collection copy)")
    s = indexed["settings"]
    write_chunks(s.paths.data_processed_dir, other, corpus_id="test-collection")
    BM25Index.build(other).save(index_dir(s.paths.indexes_dir, "test-collection", BM25_KIND))
    build_dense_index(other, index_dir(s.paths.indexes_dir, "test-collection", DENSE_KIND), HashingEmbedder())

    core = load(indexed).hybrid("reasonable restrictions")
    collection = load(indexed, corpus_id="test-collection").hybrid("reasonable restrictions")
    assert {x.chunk.corpus_id for x in core} == {CURATED_CORPUS_ID}
    assert {x.chunk.corpus_id for x in collection} == {"test-collection"}
    with pytest.raises(ValidationError):
        load(indexed, corpus_id="../escape")
