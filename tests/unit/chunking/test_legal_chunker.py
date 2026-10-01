import re

import pytest

from constitutional_evidence_rag.chunking.legal_chunker import ChunkingError, chunk_document, count_tokens
from constitutional_evidence_rag.chunking.structure import detect_sections, document_lines
from constitutional_evidence_rag.common.chunks import Chunk, ChunkingMethod, Division
from constitutional_evidence_rag.common.config import ChunkingSettings
from constitutional_evidence_rag.common.models import SourceType

from chunk_fixtures import CONSTITUTION, CONSTITUTION_ARTICLES, MODERN_JUDGMENT, SCR_JUDGMENT, SMALL, make_document

C, J = SourceType.CONSTITUTIONAL_TEXT, SourceType.JUDGMENT
DEFAULT = ChunkingSettings()
CHUNK_ID = re.compile(r"^[A-Z]+-[0-9A-Za-z]+@v\d+:\d{4}:[0-9a-f]{8}$")


def chunks_of(pages, source_type=C, settings=DEFAULT, **kw):
    document, metadata = make_document(pages, source_type, **kw)
    return chunk_document(document, metadata, settings)


def long_article(number="19", clauses=6, sentences_per_clause=4):
    body = "\n".join(
        f"({c}) " + " ".join(f"Clause {c} sentence {s} states a long rule about citizens and the State." for s in range(sentences_per_clause))
        for c in range(1, clauses + 1)
    )
    return f"PART III\nFUNDAMENTAL RIGHTS\n{number}. Protection of certain rights.—{body}\n20. Protection in respect of conviction.—No person shall be convicted."


def fresh_lines(chunks):
    """Each chunk's lines minus the overlap it repeats from the previous chunk of the same unit."""
    out, previous = [], None
    for c in chunks:
        lines = c.text.splitlines()
        if previous is not None and c.chunking_method is not ChunkingMethod.STRUCTURE and previous.article_number == c.article_number \
                and previous.heading == c.heading and previous.chunking_method is c.chunking_method:
            prev_lines = previous.text.splitlines()
            k = max((k for k in range(0, min(len(lines), len(prev_lines)) + 1) if prev_lines[len(prev_lines) - k:] == lines[:k]), default=0)
            lines = lines[k:]
        out.extend(lines)
        previous = c
    return out


# ------------------------------------------------------------------ boundaries


def test_constitution_articles_are_chunk_boundaries():
    chunks = chunks_of(CONSTITUTION)

    numbers = [c.article_number for c in chunks if c.article_number]
    assert numbers == CONSTITUTION_ARTICLES  # each (short) article is exactly one chunk
    for c in chunks:
        if c.article_number:
            assert c.chunking_method is ChunkingMethod.STRUCTURE
            starts = [l for l in c.text.splitlines() if re.match(r"^(?:\d{0,2}\[)?\d{1,3}[A-Z]*\.\s+[\[A-Z]", l)]
            assert len(starts) == 1, starts  # never two article headings in one chunk


def test_long_article_splits_within_the_article_without_crossing_into_the_next():
    chunks = chunks_of([long_article()], settings=SMALL)
    art19 = [c for c in chunks if c.article_number == "19"]
    art20 = [c for c in chunks if c.article_number == "20"]

    assert len(art19) > 1 and all(c.chunking_method is ChunkingMethod.STRUCTURE_SPLIT for c in art19)
    assert len(art20) == 1 and art20[0].text.startswith("20. Protection")
    assert all(c.token_count <= SMALL.max_tokens for c in chunks)
    assert not any("20. Protection" in c.text for c in art19)  # no overlap across the article boundary
    assert all(c.part == "PART III — FUNDAMENTAL RIGHTS" and c.article_title == "Protection of certain rights" for c in art19)


def test_split_article_labels_each_clause_once():
    chunks = [c for c in chunks_of([long_article(clauses=4, sentences_per_clause=2)], settings=SMALL) if c.article_number == "19"]

    assert len(chunks) > 1
    labels = [label for c in chunks for label in c.clause_labels]
    assert labels == ["(1)", "(2)", "(3)", "(4)"]  # every clause counted once, in order, despite overlap


def wrapped_article(lines=40):
    """An article laid out like real PDF text: many short wrapped lines (~10 tokens each)."""
    body = "\n".join(f"line {i} of the article wraps here and" for i in range(lines))
    return f"PART III\nFUNDAMENTAL RIGHTS\n19. Protection of certain rights.—{body}\nends.\n20. Next article.—Text."


def test_consecutive_split_chunks_overlap_by_whole_lines_within_the_limit():
    chunks = [c for c in chunks_of([wrapped_article()], settings=SMALL) if c.article_number == "19"]
    assert len(chunks) > 1

    for a, b in zip(chunks, chunks[1:]):
        a_lines, b_lines = a.text.splitlines(), b.text.splitlines()
        shared = next(k for k in range(len(b_lines), -1, -1) if a_lines[len(a_lines) - k:] == b_lines[:k])
        assert shared >= 1
        assert count_tokens("\n".join(b_lines[:shared])) <= SMALL.overlap_tokens


def test_judgment_section_boundaries_and_labels():
    chunks = chunks_of(SCR_JUDGMENT, J, title="Example v. State of Example")

    assert [(c.division, c.heading, c.opinion_author) for c in chunks] == [
        (Division.FRONT_MATTER, None, None), (Division.FRONT_MATTER, "HELD", None),
        (Division.OPINION, None, "VERMA, CJI."), (Division.OPINION, None, "AHMADI, CJ.")]
    assert all(c.case_name == "Example v. State of Example" and c.article_number is None for c in chunks)
    assert not any("VERMA, CJI." in c.text and "AHMADI, CJ." in c.text for c in chunks)  # opinions never mixed


def test_modern_judgment_chunks_carry_verbatim_headings():
    chunks = chunks_of(MODERN_JUDGMENT, J)

    assert [c.heading for c in chunks] == [None, "A. Facts", "B. Issues for consideration",
                                           "Submissions of the appellant", "Conclusion"]


# ------------------------------------------------------------------ provenance


def test_chunk_spanning_pages_records_page_range_and_labels():
    pages = ["PART III\nFUNDAMENTAL RIGHTS\n14. Equality before law.—The State shall not deny to any person",
             "equality before the law or the equal protection of the laws within the territory of India.",
             "15. Prohibition of discrimination.—The State shall not discriminate."]
    chunks = chunks_of(pages, labels=["225", "226", "227"])
    art14 = next(c for c in chunks if c.article_number == "14")

    assert (art14.page_start, art14.page_end) == (1, 2)
    assert (art14.page_label_start, art14.page_label_end) == ("225", "226")
    assert next(c for c in chunks if c.article_number == "15").page_start == 3


def test_every_chunk_carries_full_provenance_and_the_corpus_id():
    document, metadata = make_document(SCR_JUDGMENT, J, corpus_id="test-collection", document_id="JUDG-00000000abcd", version=3)
    chunks = chunk_document(document, metadata, DEFAULT)

    for c in chunks:
        assert (c.corpus_id, c.document_id, c.document_version, c.source_type, c.source_url) == (
            "test-collection", "JUDG-00000000abcd", 3, J, metadata.source_url)
        assert 1 <= c.page_start <= c.page_end <= document.page_count
        assert c.paragraph_number is None  # FR-03 field present, explicitly null (not detected, D16)


def test_chunk_pages_match_the_pages_their_lines_came_from():
    document, metadata = make_document(CONSTITUTION)
    lines = iter(document_lines(document))  # walk in order: the same text can occur on two pages
    for c in chunk_document(document, metadata, DEFAULT):  # default size: no splits, so no overlap
        pages = [next(lines).page for _ in c.text.splitlines()]
        assert (c.page_start, c.page_end) == (min(pages), max(pages))
    assert next(lines, None) is None


def test_mismatched_metadata_is_rejected():
    document, _ = make_document(CONSTITUTION)
    _, other = make_document(CONSTITUTION, version=2)

    with pytest.raises(ChunkingError):
        chunk_document(document, other, DEFAULT)


# ------------------------------------------------------------------ sizes, fallback, preservation


@pytest.mark.parametrize("pages,source_type", [(CONSTITUTION, C), (SCR_JUDGMENT, J), (MODERN_JUDGMENT, J), ([long_article()], C)])
def test_no_chunk_exceeds_max_tokens_and_counts_are_accurate(pages, source_type):
    for c in chunks_of(pages, source_type, SMALL):
        assert c.token_count == count_tokens(c.text) <= SMALL.max_tokens


def test_unstructured_document_uses_fallback_packing():
    sentences = [f"Line {i} of an amendment act describing changes to several provisions in detail." for i in range(40)]
    chunks = chunks_of(["\n".join(sentences[:20]), "\n".join(sentences[20:])], SourceType.AMENDMENT, SMALL)

    assert len(chunks) > 1 and all(c.chunking_method is ChunkingMethod.FALLBACK for c in chunks)
    assert all(c.division is None and c.heading is None and c.article_number is None for c in chunks)
    assert fresh_lines(chunks) == sentences  # every line kept, in order, none duplicated beyond overlap


@pytest.mark.parametrize("pages,source_type", [(CONSTITUTION, C), (SCR_JUDGMENT, J), (MODERN_JUDGMENT, J)])
def test_no_text_is_lost_or_reordered(pages, source_type):
    document, metadata = make_document(pages, source_type)
    expected = [l.text for l in document_lines(document)]

    assert fresh_lines(chunk_document(document, metadata, SMALL)) == expected


def test_single_over_long_line_is_split_by_words():
    line = " ".join(f"word{i}" for i in range(200))
    chunks = chunks_of([line], SourceType.AMENDMENT, SMALL)

    assert len(chunks) > 1 and all(c.token_count <= SMALL.max_tokens for c in chunks)
    assert " ".join(c.text for c in chunks).split()[:5] == ["word0", "word1", "word2", "word3", "word4"]


def test_empty_and_very_short_pages():
    pages = ["PART III\nFUNDAMENTAL RIGHTS\n14. Equality before law.—The State shall not deny",
             "", "   ", "7", "equality before the law."]
    chunks = chunks_of(pages)

    assert len(chunks) == 1
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 5)
    assert "7" not in chunks[0].text.splitlines()  # a bare page number is furniture


def test_document_with_no_text_after_cleanup_yields_no_chunks():
    assert chunks_of(["", "12", "   "], SourceType.AMENDMENT) == []


# ------------------------------------------------------------------ determinism


def test_chunk_ids_are_deterministic_unique_and_well_formed():
    first, second = chunks_of(CONSTITUTION, settings=SMALL), chunks_of(CONSTITUTION, settings=SMALL)

    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len({c.chunk_id for c in first}) == len(first)
    assert all(CHUNK_ID.match(c.chunk_id) for c in first)
    assert [c.chunk_index for c in first] == list(range(len(first)))
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]


def test_chunk_id_changes_when_text_changes():
    base = chunks_of(CONSTITUTION)
    edited = list(CONSTITUTION)
    edited[3] = edited[3].replace("personal\nliberty", "personal\nfreedom")
    changed = chunks_of(edited)

    diff = [(a.article_number, a.chunk_id != b.chunk_id) for a, b in zip(base, changed) if a.chunk_id != b.chunk_id]
    assert diff == [("21", True)]  # only the edited article's chunk ID changes


def test_chunk_id_includes_the_document_version():
    v1, v2 = chunks_of(CONSTITUTION, version=1), chunks_of(CONSTITUTION, version=2)

    assert all("@v1:" in a.chunk_id and "@v2:" in b.chunk_id for a, b in zip(v1, v2))


# ------------------------------------------------------------------ Chunk model


def _chunk(**over):
    fields = dict(chunk_id="X@v1:0000:00000000", corpus_id="constitutional-core", document_id="X", document_version=1,
                  source_type=C, source_url="https://e.org", chunk_index=0, page_start=1, page_end=1,
                  text="Some text.", token_count=3, chunking_method=ChunkingMethod.STRUCTURE)
    fields.update(over)
    return Chunk(**fields)


@pytest.mark.parametrize("over", [
    {"page_start": 3, "page_end": 2}, {"text": "   "}, {"corpus_id": "../x"},
    {"source_type": J, "article_number": "21"}, {"token_count": 0}, {"page_start": 0},
])
def test_chunk_model_rejects_invalid_values(over):
    with pytest.raises(ValueError):
        _chunk(**over)


def test_chunk_model_round_trips_through_json():
    chunk = _chunk(article_number="21A", clause_labels=["(1)"], division=Division.BODY)
    assert Chunk.model_validate_json(chunk.model_dump_json()) == chunk
