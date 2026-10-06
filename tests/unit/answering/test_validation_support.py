"""Phase 6 support validator: deterministic, conservative, no LLM, no network."""
import copy

import numpy as np
import pytest

from constitutional_evidence_rag.common.config import EvidenceSettings, EvidenceValidationSettings
from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.common.retrieval import RetrievalMethod, RetrievedChunk
from constitutional_evidence_rag.common.validation import SupportStatus
from constitutional_evidence_rag.query.understanding import CaseIndex, analyze_query
from constitutional_evidence_rag.reranking.evidence_selector import select_evidence
from constitutional_evidence_rag.validation.support import SupportValidator, content_terms, stem

from answer_fixtures import TITLES, make_chunks, registry_rows

S, C, I = SupportStatus.SUPPORTED, SupportStatus.CONTRADICTED, SupportStatus.INSUFFICIENT


@pytest.fixture(scope="module")
def ev():
    """E1 Constitution Art. 21 · E2 Maneka (procedure / Art. 21 & 14) · E3 writ case (Art. 32)
    · E4 Maneka (Art. 14 arbitrariness) · E5, E6 Kesavananda."""
    chunks = make_chunks()
    reg = {(m.document_id, m.document_version): m for m in registry_rows()}
    cases = CaseIndex((d, t) for d, (t, st, _) in TITLES.items() if st is SourceType.JUDGMENT)
    hits = [RetrievedChunk(chunk=c, method=RetrievalMethod.HYBRID, rank=r, score=1 / (60 + r)) for r, c in enumerate(chunks, 1)]
    items = select_evidence(analyze_query("What is Article 21?", cases), hits, chunks, reg, EvidenceSettings()).by_id()
    assert items["E1"].chunk.article_number == "21" and "Maneka" in items["E2"].document_title
    return items


def check(ev, claim, ids, **settings):
    return SupportValidator(EvidenceValidationSettings(**settings)).validate(claim, ids, ev)


# ------------------------------------------------------------------ the three states


def test_clearly_supported_claim_with_valid_citation(ev):
    r = check(ev, "Article 21 protects life and personal liberty.", ["E1"])
    assert (r.status, r.score, r.supporting_ids) == (S, 1.0, ["E1"]) and "[E1]" in r.reason


def test_negatively_phrased_provision_paraphrase_is_supported(ev):
    r = check(ev, "No person may be deprived of life or personal liberty except according to procedure established by law.", ["E1"])
    assert r.status is S


def test_clearly_unsupported_claim(ev):
    r = check(ev, "Article 21 guarantees free legal aid to every prisoner.", ["E1"])
    assert r.status is I and r.coverage < 0.75 and "Not found" in r.reason


@pytest.mark.parametrize("claim,phrase", [
    ("Article 21 provides that personal liberty can be taken away without legal procedure.", "without"),  # the brief's example
    ("The right to life under Article 21 is absolute.", "absolute"),
    ("Article 21 does not protect personal liberty.", "denies"),
    ("Article 14 does not strike at arbitrariness.", "denies"),
])
def test_clearly_contradicted_claims(ev, claim, phrase):
    ids = ["E4"] if "14" in claim else ["E1"]
    r = check(ev, claim, ids)
    assert r.status is C and phrase in r.reason and r.score == 0.0


def test_contradiction_needs_a_shared_subject(ev):
    # "invalid" vs a passage that never discusses the claim's subject: not a contradiction, just unsupported
    r = check(ev, "The Passports Act order was invalid.", ["E1"])
    assert r.status is I


def test_citation_to_the_wrong_kind_of_source_is_not_supported(ev):
    # the Phase 5 limitation: a case-law claim citing a Constitution passage
    r = check(ev, "Maneka Gandhi established that procedure must be fair.", ["E1"])
    assert r.status is I and "Maneka" in r.reason
    assert check(ev, "Maneka Gandhi held that procedure under Article 21 must be right, just and fair.", ["E2"]).status is S


# ------------------------------------------------------------------ citations


def test_nonexistent_citation(ev):
    r = check(ev, "Article 21 protects life and personal liberty.", ["E9"])
    assert r.status is I and "[E9]" in r.reason and "not among the retrieved evidence" in r.reason


def test_missing_evidence_entirely(ev):
    assert check({}, "Article 21 protects life and personal liberty.", ["E1"]).status is I


def test_no_citation(ev):
    r = check(ev, "Article 21 protects life and personal liberty.", [])
    assert r.status is I and "cites no evidence" in r.reason


def test_duplicate_citations_are_deduplicated(ev):
    r = check(ev, "Article 21 protects life and personal liberty.", ["E1", "E1", "E1"])
    assert r.status is S and r.supporting_ids == ["E1"]


def test_multiple_citations_union_and_non_contributing_citation_dropped(ev):
    joint = check(ev, "Article 21 and Article 14 are not mutually exclusive, and Article 14 strikes at arbitrariness.", ["E2", "E4"])
    assert joint.status is S and set(joint.supporting_ids) == {"E2", "E4"}
    extra = check(ev, "Article 21 protects life and personal liberty.", ["E1", "E3"])
    assert extra.status is S and extra.supporting_ids == ["E1"]  # [E3] contributes nothing and is dropped


# ------------------------------------------------------------------ quotations and exact matches


def test_quoted_evidence(ev):
    good = check(ev, 'Article 21 says "No person shall be deprived of his life or personal liberty" except by law.', ["E1"])
    assert good.status is S
    fake = check(ev, 'Article 21 says "no person may ever lose personal liberty for any reason".', ["E1"])
    assert fake.status is I and "not verbatim" in fake.reason


def test_exact_evidence_match(ev):
    r = check(ev, "Article 14 strikes at arbitrariness in State action and ensures fairness and equality of treatment.", ["E4"])
    assert (r.status, r.score) == (S, 1.0) and "verbatim" in r.reason


# ------------------------------------------------------------------ degenerate input


@pytest.mark.parametrize("claim", ["", "   ", "[E1]", "[E1] [E2]."])
def test_empty_claim(ev, claim):
    assert check(ev, claim, ["E1"]).status is I


def test_empty_evidence_text(ev):
    blank = {"E1": ev["E1"].model_copy(update={"chunk": ev["E1"].chunk.model_copy(update={"text": "   "})})}
    r = check(blank, "Article 21 protects life and personal liberty.", ["E1"])
    assert r.status is I and "no text" in r.reason


def test_too_little_content(ev):
    assert check(ev, "This is so.", ["E1"]).status is I


def test_evidence_is_never_modified(ev):
    before = copy.deepcopy({k: v.model_dump() for k, v in ev.items()})
    for claim in ["Article 21 protects life and personal liberty.", "Article 21 does not protect personal liberty."]:
        check(ev, claim, ["E1", "E2"])
    assert {k: v.model_dump() for k, v in ev.items()} == before


# ------------------------------------------------------------------ thresholds


def test_coverage_threshold_boundary(ev):
    claim = "Article 21 protects liberty forever."  # 3 of 4 key terms in E1 -> coverage exactly 0.75
    assert check(ev, claim, ["E1"]).coverage == pytest.approx(0.75)
    assert check(ev, claim, ["E1"], support_coverage=0.75).status is S
    assert check(ev, claim, ["E1"], support_coverage=0.76).status is I


class _AngleEmbedder:
    """Query vector fixed; every passage at a chosen cosine to it."""

    def __init__(self, cosine):
        self.cosine = cosine

    def embed_query(self, text):
        return np.array([1.0, 0.0], dtype=np.float32)

    def embed_documents(self, texts):
        return np.array([[self.cosine, np.sqrt(1 - self.cosine ** 2)]] * len(texts), dtype=np.float32)


def semantic(ev, claim, ids, cosine, **settings):
    v = SupportValidator(EvidenceValidationSettings(method="semantic", **settings), embedder_factory=lambda: _AngleEmbedder(cosine))
    return v.validate(claim, ids, ev)


def test_semantically_similar_paraphrase_path(ev):
    claim = "Article 21 protects life and personal liberty from arbitrary executive interference."  # 5/8 terms: below 0.75, above 0.5
    assert check(ev, claim, ["E1"]).status is I
    r = semantic(ev, claim, ["E1"], 0.9)
    assert r.status is S and r.semantic_score == pytest.approx(0.9, abs=1e-6)


def test_low_similarity_vetoes_lexical_support(ev):
    r = semantic(ev, "Article 21 protects life and personal liberty.", ["E1"], 0.2)
    assert r.status is I and "semantic" in r.reason


def test_semantic_threshold_boundary(ev):
    claim = "Article 21 protects life and personal liberty from arbitrary executive interference."
    score = semantic(ev, claim, ["E1"], 0.85).semantic_score
    assert semantic(ev, claim, ["E1"], 0.85, semantic_support_threshold=score).status is S
    assert semantic(ev, claim, ["E1"], 0.85, semantic_support_threshold=score + 1e-4).status is I


def test_semantic_method_without_model_fails_loudly(ev):
    with pytest.raises(RuntimeError, match="no embedding model"):
        SupportValidator(EvidenceValidationSettings(method="semantic")).validate("Article 21 protects life and personal liberty.", ["E1"], ev)


def test_stemming_and_terms():
    assert stem("protects") == stem("protection") == "protect"
    assert stem("deprived") == stem("deprivation")
    assert stem("procedure") == stem("procedural") and stem("constitutional") == stem("constitution")
    assert stem("personal") != stem("person")  # no false matches between different words
    assert "21" in content_terms("What Article 21 says") and "article" not in content_terms("Article 21 protects")
