import json

import pytest

from constitutional_evidence_rag.citations.citation_builder import build_citation
from constitutional_evidence_rag.common.answer import AnswerSection, AnswerStatus, ClaimBasis
from constitutional_evidence_rag.common.evidence import EvidenceRole, QueryType
from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.generation.answer import render_text
from constitutional_evidence_rag.generation.grounding import GroundingError, validate_answer
from constitutional_evidence_rag.generation.llm import LLMProviderError, LLMTimeoutError
from constitutional_evidence_rag.generation.llm_generator import parse_payload, MalformedOutputError

from llm_fakes import ART21, ScriptedLLM, llm_pipeline, reply

GOOD = reply(direct=[(f'Article 21 provides that "{ART21}" [E1]', ["E1"], "explicit")],
             explanation=[("Maneka Gandhi read Article 21 together with Article 14 [E2].", ["E2"], "inference")])


def answer(built, *replies, query="What does Article 21 provide?", validation=None, **llm):
    fake = ScriptedLLM(*replies)
    return llm_pipeline(built, fake, validation=validation, **llm).answer(query), fake


# ------------------------------------------------------------------ success and provenance


def test_successful_generation(built):
    a, fake = answer(built, GOOD)

    assert a.status is AnswerStatus.ANSWERED and a.citation_style == "evidence_id" and len(fake.requests) == 1
    assert [c.section for c in a.claims] == [AnswerSection.ANSWER, AnswerSection.JUDICIAL_INTERPRETATION]
    assert [c.basis for c in a.claims] == [ClaimBasis.EXPLICIT, ClaimBasis.INFERENCE]
    assert a.generator == "llm/scripted/test-model/grounded-v1"
    assert a.summary()["citations"] == ["E1", "E2"]


def test_citations_are_built_from_evidence_not_from_the_model(built):
    hostile = json.dumps({"status": "answered", "direct_answer": [{
        "text": "Article 21 protects personal liberty [E1].",
        "citations": [{"id": "E1", "page": 999, "title": "Invented v. Case", "url": "https://fake"}],
        "page": 999, "case": "Invented v. Case"}]})
    a, _ = answer(built, hostile)
    by_id = {e.evidence_id: e for e in a.evidence}

    c = a.citations[0]
    assert c == build_citation(1, by_id["E1"])  # every field from the evidence item
    assert c.page_start != 999 and "Invented" not in c.title and c.source_url != "https://fake"
    validate_answer(a, _evidence(a))


def test_evidence_ids_are_the_phase4_ids(built):
    a, fake = answer(built, GOOD)
    sent = fake.requests[0].user

    assert [e.evidence_id for e in a.evidence] == [f"E{n}" for n in range(1, len(a.evidence) + 1)]
    assert all(f"[{e.evidence_id}]\nSource:" in sent for e in a.evidence)
    assert {c.evidence_id for c in a.citations} <= {e.evidence_id for e in a.evidence}


# ------------------------------------------------------------------ structured output parsing


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "Here is the answer:\n{}\nThanks."])
def test_payload_parsing_tolerates_fences_and_prose(wrapper):
    p = parse_payload(wrapper.replace("{}", GOOD))
    assert p.status == "answered" and p.direct_answer[0].citations == ["E1"]


def test_citation_id_variants_are_normalized(built):
    # Phase 5 behaviour under test (marker normalization), so Phase 6 is switched off explicitly;
    # with Phase 6 on, the non-supporting [E2] is dropped — see test_validation_pipeline.py.
    a, _ = answer(built, reply(direct=[("Article 21 protects personal liberty [e1, E2].", ["[E1]", "e2"], "explicit")]),
                  validation={"enabled": False})
    assert "[E1] [E2]" in a.claims[0].statement and [c.evidence_id for c in a.citations] == ["E1", "E2"]


@pytest.mark.parametrize("bad", ["no json here", "{not: valid}", json.dumps({"status": "maybe"}),
                                 json.dumps({"status": "answered"})])
def test_malformed_output_is_detected(bad):
    with pytest.raises(MalformedOutputError):
        parse_payload(bad)


def test_malformed_reply_is_retried_then_succeeds(built):
    a, fake = answer(built, "Sorry, I cannot produce JSON.", GOOD)
    assert a.status is AnswerStatus.ANSWERED and len(fake.requests) == 2
    assert "could not be used" in fake.requests[1].user


def test_persistently_malformed_reply_fails_safely(built):
    a, fake = answer(built, "still not json", max_retries=2)
    assert a.status is AnswerStatus.GENERATION_FAILED and len(fake.requests) == 3
    assert not a.claims and not a.citations and a.evidence


# ------------------------------------------------------------------ hallucinated citations and quotes


def test_fabricated_citation_id_removes_the_statement(built):
    a, _ = answer(built, reply(direct=[(f'Article 21 provides that "{ART21}" [E1]', ["E1"], "explicit")],
                               explanation=[("Golaknath overruled this in 1967 [E99].", ["E99"], "explicit")]))
    assert a.status is AnswerStatus.PARTIAL and len(a.claims) == 1
    assert any("[E99]" in n and "not among the evidence" in n for n in a.notices)
    assert all(c.evidence_id != "E99" for c in a.citations)


def test_fabricated_quotation_removes_the_statement(built):
    a, _ = answer(built, reply(direct=[('Article 21 states "every person has an absolute and unlimited right to liberty" [E1].', ["E1"], "explicit"),
                                       ("Article 21 concerns life and personal liberty [E1].", ["E1"], "explicit")]))
    assert len(a.claims) == 1 and "absolute" not in a.claims[0].statement
    assert any("not verbatim" in n for n in a.notices)


def test_uncited_statement_is_removed(built):
    a, _ = answer(built, reply(direct=[("Article 21 protects personal liberty [E1].", ["E1"], "explicit"),
                                       ("The right is absolute.", [], "explicit")]))
    assert len(a.claims) == 1 and any("cites no evidence" in n for n in a.notices)


def test_all_statements_invalid_means_insufficient(built):
    a, _ = answer(built, reply(direct=[("Something from memory [E42].", ["E42"], "explicit")]))
    assert a.status is AnswerStatus.INSUFFICIENT_EVIDENCE and not a.claims and not a.citations
    assert any("None of the model's statements could be verified" in n for n in a.notices)


def test_citing_evidence_that_was_not_sent_is_rejected(built):
    fake = ScriptedLLM(reply(direct=[("Article 21 protects personal liberty [E1].", ["E1"], "explicit"),
                                     ("Something else [E2].", ["E2"], "explicit")]))
    a = llm_pipeline(built, fake, generation={"context_max_items": 1}).answer("What does Article 21 provide?")

    assert "[E2]\nSource:" not in fake.requests[0].user  # only E1 was sent
    assert len(a.claims) == 1 and any("[E2]" in n and "not sent to the model" in n for n in a.notices)


def test_validator_rejects_tampered_llm_answers(built):
    a, _ = answer(built, GOOD)
    ev = _evidence(a)
    validate_answer(a, ev)
    moved = a.citations[0].model_copy(update={"page_start": 1})
    with pytest.raises(GroundingError, match="metadata differs"):
        validate_answer(a.model_copy(update={"citations": [moved] + a.citations[1:]}), ev)
    fake_quote = a.claims[0].model_copy(update={"statement": 'Article 21 says "the State must provide free housing to all" [E1]'})
    with pytest.raises(GroundingError, match="not found verbatim"):
        validate_answer(a.model_copy(update={"claims": [fake_quote] + a.claims[1:]}), ev)
    no_marker = a.claims[0].model_copy(update={"statement": "Article 21 protects liberty."})
    with pytest.raises(GroundingError, match=r"\[E1\]"):
        validate_answer(a.model_copy(update={"claims": [no_marker] + a.claims[1:]}), ev)


# ------------------------------------------------------------------ insufficient evidence and failures


def test_model_reports_insufficient_evidence(built):
    a, _ = answer(built, reply(status="insufficient_evidence", reason="the passages do not discuss emergencies"))
    assert a.status is AnswerStatus.INSUFFICIENT_EVIDENCE and not a.claims and a.evidence
    assert any("do not discuss emergencies" in n for n in a.notices)


@pytest.mark.parametrize("query", ["What is the capital of France?", "What did Golaknath v. State of Punjab hold?"])
def test_weak_or_empty_evidence_never_reaches_the_model(built, query):
    a, fake = answer(built, GOOD, query=query)
    assert a.status is AnswerStatus.INSUFFICIENT_EVIDENCE and fake.requests == []


@pytest.mark.parametrize("error,status_text", [(LLMProviderError("provider returned HTTP 503: busy"), "request failed"),
                                               (LLMTimeoutError("no response within 60 s"), "did not respond in time")])
def test_provider_failure_and_timeout_fail_safely(built, error, status_text):
    a, _ = answer(built, error)
    assert a.status is AnswerStatus.GENERATION_FAILED and not a.claims and not a.citations
    assert any(status_text in n for n in a.notices) and a.evidence
    text = render_text(a)
    assert "No answer was generated" in text and "Evidence considered" in text


# ------------------------------------------------------------------ propositional vs interpretational


def test_propositional_query(built):
    a, fake = answer(built, GOOD, query="What does Article 21 provide?")
    assert a.query_type is QueryType.PROVISION and "Lead with the provision's own text" in fake.requests[0].user
    assert a.evidence[0].role is EvidenceRole.CONSTITUTIONAL_TEXT and a.citations[0].source_type is SourceType.CONSTITUTIONAL_TEXT


def test_interpretational_query(built):
    r = reply(direct=[("The Court read Article 21 as requiring a procedure that is right, just and fair [E1].", ["E1"], "inference")],
              explanation=[(f'Article 21 itself provides that "{ART21}" [E{0}]', [], "explicit")])
    first = ScriptedLLM(GOOD)
    probe = llm_pipeline(built, first).answer("How has the Supreme Court interpreted Article 21?")
    const = next(e.evidence_id for e in probe.evidence if e.role is EvidenceRole.CONSTITUTIONAL_TEXT)
    r = r.replace("[E0]", f"[{const}]")
    a, fake = answer(built, r, query="How has the Supreme Court interpreted Article 21?")
    assert a.query_type is QueryType.INTERPRETATION and "Lead with what the supplied judgment passages say" in fake.requests[0].user
    assert a.evidence[0].role is EvidenceRole.JUDICIAL
    assert [c.section for c in a.claims] == [AnswerSection.ANSWER, AnswerSection.CONSTITUTIONAL_SOURCE]


def test_rendered_answer_uses_evidence_ids(built):
    a, _ = answer(built, GOOD)
    text = render_text(a)
    assert "[E1] The Constitution of India" in text and "(inference) Maneka Gandhi" in text
    assert "(quoted passages)" not in text and "not legal advice" in text


def _evidence(answer):
    from constitutional_evidence_rag.common.evidence import EvidenceSet
    return EvidenceSet(query=answer.query, query_type=answer.query_type, items=answer.evidence, candidates_considered=len(answer.evidence))
