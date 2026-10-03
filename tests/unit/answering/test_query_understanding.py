import pytest

from constitutional_evidence_rag.common.evidence import QueryType
from constitutional_evidence_rag.query.understanding import CaseIndex, analyze_query, extract_article_refs

CASES = CaseIndex([("J1", "Maneka Gandhi v. Union of India"),
                   ("J2", "His Holiness Kesavananda Bharati Sripadagalvaru v. State of Kerala"),
                   ("J3", "T.M.A. Pai Foundation and Ors. v. State of Karnataka and Ors.")])


@pytest.mark.parametrize("query,expected", [
    ("What is Article 21?", QueryType.PROVISION),
    ("What does Article 14 provide?", QueryType.PROVISION),
    ("What is Article 32?", QueryType.PROVISION),
    ("What does Article 32 say about the Supreme Court?", QueryType.PROVISION),  # "Supreme Court" alone is no cue
    ("How has the Supreme Court interpreted Article 21?", QueryType.INTERPRETATION),
    ("How have courts interpreted Article 14?", QueryType.INTERPRETATION),
    ("What did the Court say about personal liberty?", QueryType.INTERPRETATION),
    ("What is the basic structure doctrine?", QueryType.INTERPRETATION),
    ("What did Maneka Gandhi v. Union of India decide?", QueryType.CASE),
    ("What is the significance of Kesavananda Bharati?", QueryType.CASE),
    ("What did Golaknath v. State of Punjab hold?", QueryType.CASE),  # case form, not in corpus
    ("Is the right to privacy protected?", QueryType.GENERAL),
])
def test_classification(query, expected):
    assert analyze_query(query, CASES).query_type is expected


def test_article_references_are_extracted_generically():
    assert extract_article_refs("Explain Articles 14, 19(1)(g) and 21A") == (["14", "19", "21A"], ["14", "19(1)(g)", "21A"])
    assert extract_article_refs("Art. 32 and article 226") == (["32", "226"], ["32", "226"])
    assert extract_article_refs("no articles here") == ([], [])


def test_case_resolution_uses_distinctive_corpus_titles():
    assert analyze_query("What did Maneka Gandhi decide?", CASES).case_document_ids == ["J1"]
    assert analyze_query("Kesavananda Bharti significance", CASES).case_document_ids == ["J2"]  # common misspelling of Bharati
    foundation = analyze_query("What is the foundation of Article 21?", CASES)  # generic word must not match Pai Foundation
    assert foundation.case_document_ids == [] and foundation.query_type is QueryType.PROVISION


def test_unknown_case_is_flagged_not_guessed():
    a = analyze_query("What did Golaknath v. State of Punjab hold?", CASES)
    assert a.names_unknown_case and a.case_document_ids == []


def test_content_terms_exclude_question_words_and_the_case_name():
    assert analyze_query("What is Article 21?", CASES).content_terms == ["21"]
    assert analyze_query("What did Maneka Gandhi v. Union of India decide?", CASES).content_terms == []
    assert analyze_query("What is the basic structure doctrine?", CASES).content_terms == ["basic", "structure", "doctrine"]


def test_classification_is_deterministic():
    q = "How has the Supreme Court interpreted Article 21?"
    assert analyze_query(q, CASES) == analyze_query(q, CASES)
