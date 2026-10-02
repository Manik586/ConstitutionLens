import hashlib

import numpy as np
import pytest

from constitutional_evidence_rag.retrieval.bm25 import BM25Index, tokenize
from constitutional_evidence_rag.retrieval.store import IndexNotFoundError, QueryError, RetrievalIndexError, read_meta

from retrieval_fixtures import make_chunks


# ------------------------------------------------------------------ tokenizer


def test_legal_reference_is_one_token_with_prefixes():
    # FR-06 acceptance: "19(2)" is tokenized as a single unit
    assert "19(2)" in tokenize("Article 19(2)")
    assert tokenize("clause 19(1)(g)") == ["clause", "19", "19(1)", "19(1)(g)"]
    assert tokenize("Article 19 (2)")[1:] == ["19", "19(2)"]  # spaced form normalizes to the same token
    assert tokenize("Article 243ZD(1)") == ["article", "243zd", "243zd(1)"]


@pytest.mark.parametrize("ocr,clean", [("19(l)(g)", "19(1)(g)"), ("l996", "1996"), ("2l", "21"), ("1O", "10")])
def test_ocr_digit_confusions_are_normalized(ocr, clean):
    assert tokenize(ocr) == tokenize(clean)


def test_lettered_clause_is_not_rewritten():
    assert "2(l)" in tokenize("Section 2(l) of the Act")  # a real lettered clause, not an OCR error


def test_tokenizer_basics():
    assert tokenize("What does Article 21A provide?") == ["does", "article", "21a", "provide"]  # stopwords dropped
    assert tokenize("ARTICLE 14") == tokenize("article 14")
    assert tokenize("Ärticle") == ["ärticle"]  # non-ASCII letters are kept, not dropped
    assert tokenize("?! ...") == []


# ------------------------------------------------------------------ build / persist


def _bytes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir())}


def test_build_save_load_roundtrip(tmp_path, chunks):
    index = BM25Index.build(chunks)
    index.save(tmp_path / "bm25")
    loaded = BM25Index.load(tmp_path / "bm25")

    assert loaded.chunk_ids == [c.chunk_id for c in chunks]
    assert sorted(p.name for p in (tmp_path / "bm25").iterdir()) == [
        "chunk_order.npy", "doc_ids.npy", "doc_lengths.npy", "meta.json", "offsets.npy", "terms.npy", "tfs.npy"]
    assert read_meta(tmp_path / "bm25")["n_chunks"] == len(chunks)
    for query in ["reasonable restrictions", "Article 19(2)", "guidelines"]:
        assert loaded.search(query, 5) == index.search(query, 5)


def test_indexing_is_byte_for_byte_reproducible(tmp_path, chunks):
    BM25Index.build(chunks).save(tmp_path / "a")
    BM25Index.build(make_chunks()).save(tmp_path / "b")

    assert _bytes(tmp_path / "a") == _bytes(tmp_path / "b")


def test_saved_files_need_no_pickle(tmp_path, chunks):
    BM25Index.build(chunks).save(tmp_path / "bm25")
    for path in (tmp_path / "bm25").glob("*.npy"):
        np.load(path, allow_pickle=False)  # raises if any array needed pickle


# ------------------------------------------------------------------ ranking


def test_exact_reference_ranks_first(chunks):
    hits = BM25Index.build(chunks).search("Article 19(2)", 3)

    assert chunks[hits[0][0]].text.startswith("The restrictions under Article 19(2)")


def test_scores_descending_positive_and_top_n_respected(chunks):
    hits = BM25Index.build(chunks).search("article law", 3)

    assert len(hits) == 3
    assert all(s > 0 for _, s in hits) and [s for _, s in hits] == sorted((s for _, s in hits), reverse=True)


def test_ties_break_by_chunk_order():
    twin = make_chunks()[:2]
    twin = [twin[0], twin[0].model_copy(update={"chunk_id": "CONST-000000000001@v1:0099:twin0000", "article_number": "99"})]
    hits = BM25Index.build(twin).search("equality", 5)

    assert [row for row, _ in hits] == [0, 1] and hits[0][1] == hits[1][1]  # metadata never changes the score


def test_unmatched_or_untokenizable_query_returns_empty(chunks):
    index = BM25Index.build(chunks)

    assert index.search("zzzz qqqq", 5) == []
    assert index.search("?!", 5) == []


def test_k1_and_b_apply_at_query_time_without_rebuild(chunks):
    index = BM25Index.build(chunks)

    assert index.search("reasonable restrictions", 3, b=0.0) != index.search("reasonable restrictions", 3, b=0.75)


@pytest.mark.parametrize("bad", [0, -1, 1.5, "3", True])
def test_invalid_top_n(chunks, bad):
    with pytest.raises(QueryError):
        BM25Index.build(chunks).search("law", bad)


def test_missing_corrupt_or_empty(tmp_path, chunks):
    with pytest.raises(IndexNotFoundError):
        BM25Index.load(tmp_path / "nothing")
    BM25Index.build(chunks).save(tmp_path / "bm25")
    np.save(tmp_path / "bm25" / "tfs.npy", np.zeros(3, dtype=np.int32))
    with pytest.raises(RetrievalIndexError):
        BM25Index.load(tmp_path / "bm25")
    with pytest.raises(RetrievalIndexError):
        BM25Index.build([])
