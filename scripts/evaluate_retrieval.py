"""Retrieval evaluation on a fixed question set (SRS §34.1; Experiment C for FR-08 reranking).

    python scripts/evaluate_retrieval.py                                   # bm25, dense, hybrid
    python scripts/evaluate_retrieval.py --rerank BAAI/bge-reranker-base   # + cross-encoder reranking
    python scripts/evaluate_retrieval.py --rerank BAAI/bge-reranker-base --rerank cross-encoder/ms-marco-MiniLM-L-6-v2

Two levels are measured:
- retrieval: the ranked list from each method (hit@1/5/10, MRR@10);
- evidence: the final evidence set the answer is built from (top_k items after Phase 4
  selection, boosts and lookups), i.e. what the UI shows: hit@top_k.
It also checks whether each method's rank-1 score separates answerable questions from
out-of-scope ones. Read-only: indexes and corpora are not modified. Writes a JSON report.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from constitutional_evidence_rag.common.config import load_settings
from constitutional_evidence_rag.common.logging import configure_logging
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.evaluation.retrieval_metrics import ModeReport, first_relevant_rank, load_questions, score_separation
from constitutional_evidence_rag.generation.pipeline import AnswerPipeline
from constitutional_evidence_rag.reranking.cross_encoder import make_cross_encoder, rerank
from constitutional_evidence_rag.retrieval.dense import make_embedder

REPO = Path(__file__).resolve().parents[1]
KS = (1, 5, 10)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions", type=Path, default=REPO / "data" / "eval" / "retrieval_v1.jsonl")
    ap.add_argument("--corpus-id", default=CURATED_CORPUS_ID)
    ap.add_argument("--rerank", action="append", default=[], metavar="MODEL", help="cross-encoder model to evaluate (repeatable)")
    ap.add_argument("--candidates", type=int, default=None, help="hybrid candidates handed to the reranker (default: config)")
    ap.add_argument("--out", type=Path, default=REPO / "data" / "eval" / "reports")
    args = ap.parse_args(argv)

    settings = load_settings()
    configure_logging("WARNING", settings.logging.format)
    questions = load_questions(args.questions)
    embedder = make_embedder(settings.embedding)
    pipe = AnswerPipeline.load(processed_root=settings.paths.data_processed_dir, indexes_root=settings.paths.indexes_dir,
                               corpus_id=args.corpus_id, settings=settings, embedder=embedder)
    retriever, ev = pipe.retriever, pipe.settings.evidence
    rrf_k = settings.retrieval.rrf_k
    n_candidates = args.candidates or settings.reranking.candidates
    top_n = max(KS)

    reports: dict[str, ModeReport] = {}
    for mode in ("bm25", "dense", "hybrid"):
        rep = reports[mode] = ModeReport(mode, KS)
        for q in questions:
            t = time.perf_counter()
            hits = getattr(retriever, mode)(q.question, top_n)
            rep.seconds[q.id] = time.perf_counter() - t
            rep.ranks[q.id] = first_relevant_rank(q, [h.chunk for h in hits])
            if hits:
                rep.top_scores[q.id] = hits[0].score

    # Evidence level: the full Phase 4 selection over the hybrid pool (what the answer uses).
    def evidence_rank(q, candidates):
        selected = pipe_select(q, candidates)
        return first_relevant_rank(q, [i.chunk for i in selected.items])

    def pipe_select(q, candidates):
        from constitutional_evidence_rag.reranking.evidence_selector import select_evidence
        analysis = pipe.analyze(q.question)
        return select_evidence(analysis, candidates, retriever.chunks, pipe.registry, ev)

    ev_reports: dict[str, ModeReport] = {"evidence[hybrid]": ModeReport("evidence[hybrid]", (ev.top_k,))}
    pools = {q.id: retriever.hybrid(q.question, ev.candidate_pool) for q in questions}
    for q in questions:
        ev_reports["evidence[hybrid]"].ranks[q.id] = evidence_rank(q, pools[q.id])

    for model in args.rerank:
        scorer = make_cross_encoder(settings.reranking.model_copy(update={"model_name": model}))
        name = f"rerank[{model.split('/')[-1]}]"
        rep = reports[name] = ModeReport(name, KS)
        ev_rep = ev_reports[f"evidence[{name}]"] = ModeReport(f"evidence[{name}]", (ev.top_k,))
        for q in questions:
            pool = pools[q.id]
            t = time.perf_counter()
            ranked = rerank(q.question, pool[:n_candidates], scorer)
            rep.seconds[q.id] = time.perf_counter() - t
            rep.ranks[q.id] = first_relevant_rank(q, [c.chunk for c, _ in ranked[:top_n]])
            if ranked:
                rep.top_scores[q.id] = ranked[0][1]
            # Reranked order expressed in RRF units so Phase 4 boosts keep their meaning.
            reordered = [c.model_copy(update={"rank": i, "score": 1.0 / (rrf_k + i)})
                         for i, (c, _) in enumerate(ranked, start=1)]
            rest = [c.model_copy(update={"rank": i, "score": 1.0 / (rrf_k + i)})
                    for i, c in enumerate(pool[n_candidates:], start=len(reordered) + 1)]
            ev_rep.ranks[q.id] = evidence_rank(q, reordered + rest)

    summary = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "corpus_id": args.corpus_id,
               "questions": args.questions.name, "n_questions": len(questions),
               "embedding_model": settings.embedding.model_name, "rerank_candidates": n_candidates,
               "retrieval": [r.summary(questions) for r in reports.values()],
               "evidence": [r.summary(questions) for r in ev_reports.values()],
               "score_separation": {n: score_separation(r, questions) for n, r in reports.items() if n != "hybrid"},
               "per_question": {q.id: {"question": q.question, **{n: r.ranks[q.id] for n, r in {**reports, **ev_reports}.items()}}
                                for q in questions if q.answerable}}
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"retrieval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n{len(questions)} questions ({sum(q.answerable for q in questions)} answerable) on {args.corpus_id}\n")
    print(f"{'retrieval mode':34s} {'all h@1':>8s} {'h@5':>6s} {'h@10':>6s} {'mrr':>6s} | {'case h@5':>8s} {'prov h@5':>8s} | {'s/query':>7s}")
    for r in summary["retrieval"]:
        a, c, p = r["all"], r["case"], r["provision"]
        print(f"{r['mode']:34s} {a['hit@1']:8.2f} {a['hit@5']:6.2f} {a['hit@10']:6.2f} {a['mrr@10']:6.2f} | "
              f"{c['hit@5']:8.2f} {p['hit@5']:8.2f} | {r.get('mean_seconds', 0):7.3f}")
    print(f"\n{'final evidence (top ' + str(ev.top_k) + ')':34s} {'all':>8s} {'case':>6s} {'prov':>6s}")
    for r in summary["evidence"]:
        k = f"hit@{ev.top_k}"
        print(f"{r['mode']:34s} {r['all'][k]:8.2f} {r['case'][k]:6.2f} {r['provision'][k]:6.2f}")
    print("\nrank-1 score: answerable [min, mean, max] vs out-of-scope [min, mean, max]; share of out-of-scope below every answerable")
    for name, sep in summary["score_separation"].items():
        if sep:
            print(f"  {name:32s} {sep['answerable']} vs {sep['out_of_scope']} -> {sep['out_of_scope_below_answerable_min']:.2f}")
    print(f"\nreport: {out.relative_to(REPO) if out.is_relative_to(REPO) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
