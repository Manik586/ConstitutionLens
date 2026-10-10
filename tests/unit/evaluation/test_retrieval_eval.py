"""Retrieval evaluation metrics and cross-encoder rerank ordering (no model download)."""
import json
import sys
from pathlib import Path

import pytest

ANSWERING = Path(__file__).resolve().parents[1] / "answering"
if str(ANSWERING) not in sys.path:
    sys.path.insert(0, str(ANSWERING))

from answer_fixtures import CONST, KESAVA, MANEKA, make_chunks  # noqa: E402

from constitutional_evidence_rag.common.retrieval import RetrievalMethod, RetrievedChunk  # noqa: E402
from constitutional_evidence_rag.evaluation.retrieval_metrics import (  # noqa: E402
    EvalQuestion, ModeReport, first_relevant_rank, load_questions, score_separation,
)
from constitutional_evidence_rag.reranking.cross_encoder import rerank  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


def chunks():
    return make_chunks()


def test_case_questions_are_judged_by_document():
    cs = chunks()
    q = EvalQuestion("q", "case", "x", relevant_documents=(MANEKA,))
    assert all(q.is_relevant(c) == (c.document_id == MANEKA) for c in cs)


def test_provision_questions_count_only_the_constitutions_own_text():
    cs = chunks()
    art21 = [c for c in cs if c.document_id == CONST and c.article_number == "21"]
    assert art21, "fixture must contain Article 21"
    q = EvalQuestion("q", "provision", "x", relevant_articles=("21",))
    assert all(q.is_relevant(c) for c in art21)
    judgments_mentioning = [c for c in cs if c.document_id != CONST and "21" in c.text]
    assert judgments_mentioning and not any(q.is_relevant(c) for c in judgments_mentioning)


def test_out_of_scope_questions_have_nothing_relevant():
    q = EvalQuestion("q", "out_of_scope", "x")
    assert not q.answerable and not any(q.is_relevant(c) for c in chunks())


def test_first_relevant_rank_and_metrics():
    cs = chunks()
    maneka = next(c for c in cs if c.document_id == MANEKA)
    kesava = next(c for c in cs if c.document_id == KESAVA)
    qs = [EvalQuestion("a", "case", "x", relevant_documents=(MANEKA,)),
          EvalQuestion("b", "case", "y", relevant_documents=(KESAVA,)),
          EvalQuestion("n", "out_of_scope", "z")]
    assert first_relevant_rank(qs[0], [kesava, maneka]) == 2
    assert first_relevant_rank(qs[1], [maneka]) is None
    rep = ModeReport("m", (1, 5))
    rep.ranks = {"a": 2, "b": None, "n": None}
    s = rep.summary(qs)
    assert s["all"] == {"hit@1": 0.0, "hit@5": 0.5, "mrr@5": 0.25, "n": 2}  # out-of-scope excluded


def test_score_separation():
    qs = [EvalQuestion("a", "case", "x", relevant_documents=(MANEKA,)), EvalQuestion("n1", "out_of_scope", "y"),
          EvalQuestion("n2", "out_of_scope", "z")]
    rep = ModeReport("dense", (1,))
    rep.top_scores = {"a": 0.7, "n1": 0.5, "n2": 0.75}
    sep = score_separation(rep, qs)
    assert sep["out_of_scope_below_answerable_min"] == 0.5
    assert score_separation(ModeReport("x", (1,)), qs) is None


def test_load_questions_validates(tmp_path):
    good = tmp_path / "q.jsonl"
    good.write_text(json.dumps({"id": "a", "kind": "case", "question": "x", "relevant_documents": ["D"]}) + "\n", encoding="utf-8")
    assert load_questions(good)[0].relevant_documents == ("D",)
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"id": "a", "kind": "case", "question": "x"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no relevance judgement"):
        load_questions(bad)
    dup = tmp_path / "dup.jsonl"
    row = json.dumps({"id": "a", "kind": "out_of_scope", "question": "x"})
    dup.write_text(row + "\n" + row + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_questions(dup)


def test_the_shipped_question_set_is_valid():
    qs = load_questions(REPO / "data" / "eval" / "retrieval_v1.jsonl")
    kinds = {k: sum(q.kind == k for q in qs) for k in ("case", "provision", "out_of_scope")}
    assert kinds == {"case": 20, "provision": 13, "out_of_scope": 6}


class FakeScorer:
    def __init__(self, scores):
        self.scores, self.calls = scores, []

    def score(self, query, passages):
        self.calls.append((query, list(passages)))
        return [self.scores[p] for p in passages]


def test_rerank_orders_by_cross_encoder_score_and_breaks_ties_by_incoming_order():
    cs = chunks()[:4]
    cands = [RetrievedChunk(chunk=c, method=RetrievalMethod.HYBRID, rank=i + 1, score=0.1) for i, c in enumerate(cs)]
    scores = {cs[0].text: 0.1, cs[1].text: 0.9, cs[2].text: 0.5, cs[3].text: 0.5}
    scorer = FakeScorer(scores)
    out = rerank("what?", cands, scorer)
    assert [c.chunk.chunk_id for c, _ in out] == [cs[1].chunk_id, cs[2].chunk_id, cs[3].chunk_id, cs[0].chunk_id]
    assert [s for _, s in out] == [0.9, 0.5, 0.5, 0.1]
    assert scorer.calls == [("what?", [c.text for c in cs])]
    assert rerank("q", [], scorer) == []
