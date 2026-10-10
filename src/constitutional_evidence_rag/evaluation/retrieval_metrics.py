"""Retrieval evaluation (SRS §34.1): document-level relevance judgements and ranking metrics.

A question is judged at document level ("case" questions: any passage of a listed judgment
counts) or at provision level ("provision" questions: only the Constitution's own text for a
listed Article counts — a judgment quoting the Article does not). "out_of_scope" questions
have no relevant passage; they are used to check whether scores can signal "nothing found".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

from constitutional_evidence_rag.common.chunks import Chunk
from constitutional_evidence_rag.common.models import SourceType

KINDS = ("case", "provision", "out_of_scope")


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    kind: str
    question: str
    relevant_documents: tuple[str, ...] = ()
    relevant_articles: tuple[str, ...] = ()

    @property
    def answerable(self) -> bool:
        return self.kind != "out_of_scope"

    def is_relevant(self, chunk: Chunk) -> bool:
        if self.kind == "case":
            return chunk.document_id in self.relevant_documents
        if self.kind == "provision":
            return chunk.source_type is SourceType.CONSTITUTIONAL_TEXT and chunk.article_number in self.relevant_articles
        return False


def load_questions(path: Path) -> list[EvalQuestion]:
    questions = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        kind = raw["kind"]
        if kind not in KINDS:
            raise ValueError(f"{path.name}:{n}: unknown kind {kind!r}")
        q = EvalQuestion(id=raw["id"], kind=kind, question=raw["question"],
                         relevant_documents=tuple(raw.get("relevant_documents", ())),
                         relevant_articles=tuple(raw.get("relevant_articles", ())))
        if q.kind == "case" and not q.relevant_documents or q.kind == "provision" and not q.relevant_articles:
            raise ValueError(f"{path.name}:{n}: {q.id} has no relevance judgement")
        questions.append(q)
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name}: duplicate question ids")
    return questions


def first_relevant_rank(question: EvalQuestion, ranked: list[Chunk]) -> int | None:
    """1-based rank of the first relevant chunk, or None."""
    for rank, chunk in enumerate(ranked, start=1):
        if question.is_relevant(chunk):
            return rank
    return None


@dataclass
class ModeReport:
    mode: str
    ks: tuple[int, ...]
    ranks: dict[str, int | None] = field(default_factory=dict)        # question id -> first relevant rank
    top_scores: dict[str, float] = field(default_factory=dict)         # question id -> score of rank 1
    seconds: dict[str, float] = field(default_factory=dict)            # question id -> query latency

    def hit_at(self, k: int, ids: list[str]) -> float:
        return mean(1.0 if (r := self.ranks[i]) is not None and r <= k else 0.0 for i in ids) if ids else 0.0

    def mrr(self, ids: list[str], cutoff: int) -> float:
        return mean(1.0 / r if (r := self.ranks[i]) is not None and r <= cutoff else 0.0 for i in ids) if ids else 0.0

    def summary(self, questions: list[EvalQuestion]) -> dict:
        out = {"mode": self.mode}
        groups = {"all": [q.id for q in questions if q.answerable],
                  "case": [q.id for q in questions if q.kind == "case"],
                  "provision": [q.id for q in questions if q.kind == "provision"]}
        for name, ids in groups.items():
            out[name] = {**{f"hit@{k}": round(self.hit_at(k, ids), 3) for k in self.ks},
                         f"mrr@{max(self.ks)}": round(self.mrr(ids, max(self.ks)), 3), "n": len(ids)}
        if self.seconds:
            out["mean_seconds"] = round(mean(self.seconds.values()), 3)
        return out


def score_separation(report: ModeReport, questions: list[EvalQuestion]) -> dict | None:
    """Can the rank-1 score tell answerable questions apart from out-of-scope ones?

    Returns the score ranges and the share of out-of-scope questions whose rank-1 score is
    below the lowest answerable rank-1 score (1.0 = a threshold separates them perfectly)."""
    answerable = [report.top_scores[q.id] for q in questions if q.answerable and q.id in report.top_scores]
    unanswerable = [report.top_scores[q.id] for q in questions if not q.answerable and q.id in report.top_scores]
    if not answerable or not unanswerable:
        return None
    floor = min(answerable)
    return {"answerable": [round(min(answerable), 3), round(mean(answerable), 3), round(max(answerable), 3)],
            "out_of_scope": [round(min(unanswerable), 3), round(mean(unanswerable), 3), round(max(unanswerable), 3)],
            "out_of_scope_below_answerable_min": round(mean(1.0 if s < floor else 0.0 for s in unanswerable), 3)}
