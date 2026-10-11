"""Misspelled case names, word forms in the coverage check, and cases missing from a corpus."""
import pytest

from constitutional_evidence_rag.common.evidence import QueryType
from constitutional_evidence_rag.query.understanding import CaseIndex, analyze_query, is_one_typo, light_stem
from constitutional_evidence_rag.reranking.evidence_selector import _coverage

from answer_fixtures import KESAVA, MANEKA, TITLES


@pytest.fixture
def cases():
    return CaseIndex([(d, TITLES[d][0]) for d in (MANEKA, KESAVA)])


@pytest.mark.parametrize("word, name, expected", [
    ("maenka", "maneka", True),            # adjacent letters swapped
    ("kesavanada", "kesavananda", True),   # one letter missing
    ("vishakha", "vishaka", True),         # one letter extra
    ("mameka", "maneka", True),            # one letter wrong
    ("reserved", "reserve", False),        # an inflection, not a typo
    ("gandhis", "gandhi", False),
    ("shiny", "shine", False),             # too short to correct safely
    ("naneka", "maneka", False),           # different first letter
    ("mamekb", "maneka", False),           # two edits
])
def test_is_one_typo(word, name, expected):
    assert is_one_typo(word, name) is expected


def test_light_stem_matches_word_forms_only_for_coverage():
    assert light_stem("argue") == light_stem("argued") == light_stem("arguing") == light_stem("argues")
    assert light_stem("rivers") == light_stem("river")
    assert light_stem("discharging") == light_stem("discharge")
    assert light_stem("tanneries") == light_stem("tannery")
    assert light_stem("class") == "class" and light_stem("process") == "process"
    assert light_stem("19(1)") == "19(1)" and light_stem("21a") == "21a"


def test_a_misspelled_case_name_resolves_and_is_recorded(cases):
    a = analyze_query("what maenka gandhi argued for?", cases)
    assert a.case_document_ids == [MANEKA] and a.query_type is QueryType.CASE
    assert a.corrections == {"maenka": "maneka"}
    assert a.search_query == "what maneka gandhi argued for?"
    assert "maenka" not in a.content_terms and "gandhi" not in a.content_terms
    assert any("spelling" in s for s in a.signals)


def test_a_misspelling_of_the_only_name_word_also_resolves(cases):
    a = analyze_query("What did the court decide in Kesavanada?", cases)
    assert a.case_document_ids == [KESAVA] and a.corrections == {"kesavanada": "kesavananda"}


def test_correct_spelling_records_no_correction(cases):
    a = analyze_query("What did Maneka Gandhi argue?", cases)
    assert a.case_document_ids == [MANEKA] and a.corrections == {} and a.search_query is None
    assert a.content_terms == []  # "argue" is question framing, not evidence to look for


def test_a_known_library_case_missing_from_this_corpus_is_flagged(cases):
    a = analyze_query("What did Maneka Gandhi argue?", CaseIndex([]), reference=cases)
    assert a.names_unknown_case and a.unknown_case_titles == ["Maneka Gandhi v. Union of India"]
    assert a.query_type is QueryType.CASE and a.case_document_ids == []
    # without a reference index nothing is flagged (the corpus simply has no such title)
    assert not analyze_query("What did Maneka Gandhi argue?", CaseIndex([])).names_unknown_case


def test_coverage_counts_word_forms_as_the_same_term(chunks):
    chunk = chunks[0].model_copy(update={"text": "Effluent discharged by the tannery polluted the river."})
    assert _coverage(["tanneries", "discharging", "effluents", "rivers"], chunk, set()) == 1.0
    assert _coverage(["tanneries", "groundwater"], chunk, set()) == 0.5


def test_pipeline_answers_a_misspelled_question_and_says_how_it_read_it(pipeline):
    answer = pipeline.answer("What did Maenka Gandhi say about Article 21?")
    assert answer.status.value in ("answered", "partial")
    assert any(n.startswith("Spelling: searched for 'maneka' instead of 'maenka'") for n in answer.notices)
    assert any(e.chunk.document_id == MANEKA for e in answer.evidence)
