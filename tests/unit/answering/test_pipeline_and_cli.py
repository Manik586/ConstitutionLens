import json
import runpy

import pytest

from constitutional_evidence_rag.common.answer import Answer, AnswerSection, AnswerStatus
from constitutional_evidence_rag.common.evidence import EvidenceRole, QueryType
from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.generation.grounding import GroundingError
from constitutional_evidence_rag.retrieval.store import QueryError

from answer_fixtures import KESAVA, MANEKA, REPO_ROOT, HashingEmbedder, write_config

query_main = runpy.run_path(str(REPO_ROOT / "scripts" / "query.py"))["main"]
build_main = runpy.run_path(str(REPO_ROOT / "scripts" / "build_indexes.py"))["main"]
FAKE = lambda settings: HashingEmbedder()


@pytest.mark.parametrize("article", ["14", "21", "32"])
def test_provision_question_end_to_end(pipeline, built, article):
    answer = pipeline.answer(f"What is Article {article}?")

    assert answer.query_type is QueryType.PROVISION and answer.status is AnswerStatus.ANSWERED
    assert answer.evidence[0].chunk.article_number == article
    assert answer.citations[0].source_type is SourceType.CONSTITUTIONAL_TEXT
    by_id = {c.chunk_id: c for c in built["chunks"]}
    for item in answer.evidence:  # provenance identical to chunks.jsonl after selection
        assert item.chunk == by_id[item.chunk.chunk_id]
    assert {c.chunk_id for c in answer.citations} <= {e.chunk.chunk_id for e in answer.evidence}


def test_interpretation_question_end_to_end(pipeline):
    answer = pipeline.answer("How has the Supreme Court interpreted Article 21?")

    assert answer.query_type is QueryType.INTERPRETATION
    assert answer.evidence[0].role is EvidenceRole.JUDICIAL
    assert any(c.section is AnswerSection.CONSTITUTIONAL_SOURCE for c in answer.claims)


def test_case_question_end_to_end(pipeline):
    answer = pipeline.answer("What did Maneka Gandhi v. Union of India decide?")
    assert answer.query_type is QueryType.CASE and answer.evidence[0].chunk.document_id == MANEKA

    kesava = pipeline.answer("What did Kesavananda Bharati establish?")
    assert kesava.evidence[0].chunk.document_id == KESAVA and kesava.claims[0].statement.startswith("According to the reporter's headnote")


def test_doctrine_question_end_to_end(pipeline):
    answer = pipeline.answer("What is the basic structure doctrine?")
    assert answer.status is AnswerStatus.ANSWERED and answer.evidence[0].chunk.document_id == KESAVA


@pytest.mark.parametrize("query,reason", [
    ("What is the capital of France?", "key terms"),
    ("What did Golaknath v. State of Punjab hold?", "not in the corpus"),
])
def test_insufficient_evidence_is_explicit(pipeline, query, reason):
    answer = pipeline.answer(query)
    assert answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert not answer.claims and not answer.citations
    assert any(reason in n for n in answer.notices) and answer.evidence  # evidence still exposed


@pytest.mark.parametrize("query", ["", "   ", None])
def test_empty_query(pipeline, query):
    with pytest.raises(QueryError):
        pipeline.answer(query)


def test_answers_are_deterministic(pipeline):
    q = "How has the Supreme Court interpreted Article 14?"
    assert pipeline.answer(q) == pipeline.answer(q)


def test_any_generator_is_grounding_checked(pipeline):
    real = pipeline.generator

    class Fabricating:
        name = "fabricating"

        def generate(self, analysis, evidence):
            answer = real.generate(analysis, evidence)
            bad = answer.citations[0].model_copy(update={"page_start": 1, "page_end": 1})
            return answer.model_copy(update={"citations": [bad] + answer.citations[1:]})

    pipeline.generator = Fabricating()
    with pytest.raises(GroundingError):
        pipeline.answer("What is Article 21?")


# ------------------------------------------------------------------ CLI


@pytest.fixture
def config(built):
    return write_config(built["tmp"])


def test_cli_answer_text_and_json(config, capsys):
    assert query_main(["What is Article 21?", "--answer", "--config", str(config)], embedder_factory=FAKE) == 0
    out = capsys.readouterr().out
    assert "Answer:" in out and "Citations:" in out and "[1] The Constitution of India" in out and "not legal advice" in out

    assert query_main(["What is Article 21?", "--answer", "--json", "--config", str(config)], embedder_factory=FAKE) == 0
    answer = Answer.model_validate(json.loads(capsys.readouterr().out))
    assert answer.query_type is QueryType.PROVISION and answer.citations


def test_cli_existing_modes_unchanged_and_bad_combinations(config, capsys):
    assert query_main(["Article 21", "--mode", "bm25", "--config", str(config)], embedder_factory=FAKE) == 0
    assert capsys.readouterr().out.startswith(" 1. [bm25=")
    assert query_main(["Article 21", "--answer", "--mode", "bm25", "--config", str(config)], embedder_factory=FAKE) == 2
    assert query_main(["   ", "--answer", "--config", str(config)], embedder_factory=FAKE) == 2


def test_cli_answer_shows_evidence_when_insufficient(config, capsys):
    assert query_main(["What is the capital of France?", "--answer", "--config", str(config)], embedder_factory=FAKE) == 0
    out = capsys.readouterr().out
    assert "insufficient" in out and "Evidence considered" in out
