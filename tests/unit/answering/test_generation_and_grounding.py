import pytest

from constitutional_evidence_rag.citations.citation_builder import build_citation, format_citation
from constitutional_evidence_rag.common.answer import AnswerSection, AnswerStatus
from constitutional_evidence_rag.common.config import EvidenceSettings, GenerationSettings
from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.common.retrieval import RetrievalMethod, RetrievedChunk
from constitutional_evidence_rag.generation.answer import ExtractiveAnswerGenerator, normalize, split_sentences
from constitutional_evidence_rag.generation.grounding import GroundingError, validate_answer
from constitutional_evidence_rag.query.understanding import CaseIndex, analyze_query
from constitutional_evidence_rag.reranking.evidence_selector import select_evidence

from answer_fixtures import TITLES, registry_rows

REGISTRY = {(m.document_id, m.document_version): m for m in registry_rows()}
CASES = CaseIndex((d, t) for d, (t, st, _) in TITLES.items() if st is SourceType.JUDGMENT)
GEN = ExtractiveAnswerGenerator(GenerationSettings())


def evidence_for(chunks, query):
    retrieved = [RetrievedChunk(chunk=c, method=RetrievalMethod.HYBRID, rank=r, score=1 / (60 + r))
                 for r, c in enumerate(chunks, start=1)]
    analysis = analyze_query(query, CASES)
    return analysis, select_evidence(analysis, retrieved, chunks, REGISTRY, EvidenceSettings())


# ------------------------------------------------------------------ citations


def test_citation_is_built_only_from_evidence_metadata(chunks):
    _, ev = evidence_for(chunks, "What is Article 21?")
    item = ev.items[0]
    c = build_citation(1, item)

    assert (c.chunk_id, c.document_id, c.page_start, c.page_end, c.source_type, c.article_number) == (
        item.chunk.chunk_id, item.chunk.document_id, item.chunk.page_start, item.chunk.page_end,
        item.chunk.source_type, "21")
    assert format_citation(c) == ("[1] The Constitution of India (as on 1st May, 2026), Article 21 "
                                  f"(Protection of life and personal liberty), PDF pp. {c.page_start}–{c.page_end}")


def test_judgment_citation_formats(chunks):
    _, ev = evidence_for(chunks, "How has the Supreme Court interpreted Article 21?")
    maneka = next(i for i in ev.items if i.document_title.startswith("Maneka"))
    c = build_citation(2, maneka)

    assert format_citation(c) == f"[2] Maneka Gandhi v. Union of India (25 January 1978), judgment text, PDF pp. {c.page_start}–{c.page_end}"
    assert "opinion of BHAGWATI, J." in format_citation(c, show_author=True)
    single = c.model_copy(update={"page_end": c.page_start, "page_label_start": "48", "page_label_end": "48"})
    assert format_citation(single).endswith(f"printed p. 48 (PDF p. {c.page_start})")


# ------------------------------------------------------------------ extractive generation


@pytest.mark.parametrize("article", ["14", "21", "32"])
def test_provision_answer_quotes_the_article_then_judgments(chunks, article):
    analysis, ev = evidence_for(chunks, f"What is Article {article}?")
    answer = GEN.generate(analysis, ev)

    lead = answer.claims[0]
    assert lead.section is AnswerSection.ANSWER and lead.statement.startswith(f"Article {article} of the Constitution")
    assert answer.citations[0].source_type is SourceType.CONSTITUTIONAL_TEXT and answer.citations[0].article_number == article
    assert any(c.section is AnswerSection.JUDICIAL_INTERPRETATION for c in answer.claims)
    validate_answer(answer, ev)


def test_quotes_are_verbatim_and_never_claim_a_holding(chunks):
    for query in ["What is Article 21?", "How has the Supreme Court interpreted Article 14?", "What is the basic structure doctrine?"]:
        analysis, ev = evidence_for(chunks, query)
        answer = GEN.generate(analysis, ev)
        sources = {c.citation_id: normalize(next(i for i in ev.items if i.evidence_id == c.evidence_id).chunk.text)
                   for c in answer.citations}
        for claim in answer.claims:
            for part in claim.quote.split("[…]"):
                assert part.strip() in sources[claim.citation_ids[0]]
            template = claim.statement.replace(f"“{claim.quote}”", "")
            assert "held" not in template.lower() and "court" not in template.lower()


def test_no_relevant_sentence_means_insufficient(chunks):
    analysis, ev = evidence_for([chunks[6]], "What is the doctrine of eclipse?")  # retrieved text never mentions it
    answer = GEN.generate(analysis, ev)
    assert answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE and not answer.claims and not answer.citations


def test_sentence_splitter_respects_legal_abbreviations():
    text = "In Maneka Gandhi v. Union of India, Bhagwati, J. read Art. 21 with Arts. 14 and 19. See [1978] 2 S.C.R. 621. Next (A.N. Ray, C.J.) here."
    assert split_sentences(text) == [
        "In Maneka Gandhi v. Union of India, Bhagwati, J. read Art. 21 with Arts. 14 and 19.",
        "See [1978] 2 S.C.R. 621.", "Next (A.N. Ray, C.J.) here."]


# ------------------------------------------------------------------ grounding


@pytest.fixture
def grounded(chunks):
    analysis, ev = evidence_for(chunks, "What is Article 21?")
    answer = GEN.generate(analysis, ev)
    validate_answer(answer, ev)
    return answer, ev


def test_cannot_cite_a_document_that_was_not_retrieved(grounded, chunks):
    answer, ev = grounded
    outsider = next(c for c in chunks if c.chunk_id not in {i.chunk.chunk_id for i in ev.items})
    forged = answer.citations[0].model_copy(update={"chunk_id": outsider.chunk_id, "document_id": outsider.document_id})
    with pytest.raises(GroundingError, match="not among the selected evidence"):
        validate_answer(answer.model_copy(update={"citations": [forged] + answer.citations[1:]}), ev)
    ghost = answer.citations[0].model_copy(update={"evidence_id": "E99"})
    with pytest.raises(GroundingError):
        validate_answer(answer.model_copy(update={"citations": [ghost] + answer.citations[1:]}), ev)


@pytest.mark.parametrize("field,value", [("page_start", 999), ("page_end", 999), ("title", "Invented v. Case"),
                                         ("article_number", "22"), ("source_url", "https://fake.example")])
def test_altered_citation_metadata_is_rejected(grounded, field, value):
    answer, ev = grounded
    tampered = answer.citations[0].model_copy(update={field: value})
    with pytest.raises(GroundingError, match="metadata differs"):
        validate_answer(answer.model_copy(update={"citations": [tampered] + answer.citations[1:]}), ev)


def test_fabricated_quote_or_citation_number_is_rejected(grounded):
    answer, ev = grounded
    claim = answer.claims[0]
    fake = claim.model_copy(update={"quote": "Every person has an absolute right to liberty.",
                                    "statement": "“Every person has an absolute right to liberty.” [1]"})
    with pytest.raises(GroundingError, match="verbatim"):
        validate_answer(answer.model_copy(update={"claims": [fake]}), ev)
    unknown = claim.model_copy(update={"citation_ids": [42], "statement": claim.statement + " [42]"})
    with pytest.raises(GroundingError, match="unknown citation"):
        validate_answer(answer.model_copy(update={"claims": [unknown]}), ev)
    unmarked = claim.model_copy(update={"statement": f"“{claim.quote}”"})
    with pytest.raises(GroundingError, match="marker"):
        validate_answer(answer.model_copy(update={"claims": [unmarked]}), ev)


def test_insufficient_answer_cannot_carry_claims(grounded):
    answer, ev = grounded
    with pytest.raises(GroundingError):
        validate_answer(answer.model_copy(update={"status": AnswerStatus.INSUFFICIENT_EVIDENCE}), ev)
