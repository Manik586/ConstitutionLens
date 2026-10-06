"""Query the curated corpus: retrieval (Phase 3) or grounded answers (Phase 4; no UI needed).

    python scripts/query.py "What does Article 21 provide?"
    python scripts/query.py "Article 19(2)" --mode bm25 --top-k 5
    python scripts/query.py "basic structure doctrine" --json
    python scripts/query.py "What is Article 21?" --answer [--show-evidence] [--json]
    python scripts/query.py "What protections are provided for personal liberty?" --mode hybrid --top-k 5 --generate

Retrieval modes: hybrid (default; BM25 + dense fused by RRF), bm25, dense. Results carry
full provenance (document, version, pages, article/heading, opinion author, source URL).
--answer runs query understanding -> evidence selection -> grounded answer with citations
(D20 - D22) using generation.backend (default: extractive quotes); it always uses hybrid retrieval.
--generate (Phase 5) does the same with the configured LLM (configs/v1.yaml `llm`, key from the
environment); --top-k then sets how many evidence items are selected and sent to the model.

Exit codes: 0 = results printed (possibly none), 2 = config/index/query error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from constitutional_evidence_rag.common.config import ConfigError, load_settings
from constitutional_evidence_rag.common.logging import configure_logging, get_logger
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.generation.answer import render_text
from constitutional_evidence_rag.generation.grounding import GroundingError
from constitutional_evidence_rag.generation.llm import LLMConfigurationError, make_llm_client
from constitutional_evidence_rag.generation.pipeline import AnswerPipeline, make_generator
from constitutional_evidence_rag.retrieval.dense import make_embedder
from constitutional_evidence_rag.retrieval.hybrid import Retriever
from constitutional_evidence_rag.retrieval.store import QueryError, RetrievalIndexError


def _describe(c) -> str:
    where = c.case_name or (f"Article {c.article_number} — {c.article_title}" if c.article_number else None) \
        or c.heading or c.part or c.document_id
    extra = f" | {c.opinion_author}" if c.opinion_author else ""
    return f"{where}{extra} | pp. {c.page_start}-{c.page_end}"


def main(argv: list[str] | None = None, embedder_factory=make_embedder, llm_client_factory=make_llm_client) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query")
    parser.add_argument("--mode", choices=["hybrid", "bm25", "dense"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=None, help="results to return (default: config top_n for the mode)")
    parser.add_argument("--json", action="store_true", help="print RetrievedChunk JSON lines (or Answer JSON with --answer)")
    parser.add_argument("--answer", action="store_true", help="return a grounded, cited answer instead of a ranked list")
    parser.add_argument("--show-evidence", action="store_true", help="with --answer/--generate: list all evidence items and scores")
    parser.add_argument("--show-validation", action="store_true",
                        help="with --generate: show the Phase 6 evidence-support result for every claim")
    parser.add_argument("--generate", action="store_true",
                        help="Phase 5: grounded answer written by the configured LLM, citing evidence as [E1], [E2]")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.logging.level, settings.logging.format)
    logger = get_logger("query")
    if args.generate:
        return _generate(args, settings, logger, embedder_factory, llm_client_factory)
    if args.answer:
        return _answer(args, settings, logger, embedder_factory)

    try:
        embedder = embedder_factory(settings.embedding) if args.mode != "bm25" else None
        retriever = Retriever.load(processed_root=settings.paths.data_processed_dir,
                                   indexes_root=settings.paths.indexes_dir, corpus_id=CURATED_CORPUS_ID,
                                   settings=settings, embedder=embedder)
        results = getattr(retriever, args.mode)(args.query, args.top_k)
    except (QueryError, RetrievalIndexError) as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — embedding model could not be loaded
        logger.error("%s: %s", type(exc).__name__, exc)
        return 2

    if args.json:
        for r in results:
            print(r.model_dump_json())
        return 0
    if not results:
        print("No results.")
    for r in results:
        parts = [f"{r.method.value}={r.score:.4f}"]
        if r.method.value == "hybrid":
            parts.append(f"bm25 #{r.bm25_rank or '-'} dense #{r.dense_rank or '-'}")
        snippet = " ".join(r.chunk.text.split())[:160]
        print(f"{r.rank:>2}. [{' | '.join(parts)}] {r.chunk_id}\n    {_describe(r.chunk)}\n    {snippet}…")
    return 0


def _generate(args, settings, logger, embedder_factory, llm_client_factory) -> int:
    if args.mode != "hybrid":
        logger.error("--generate answers from Phase 4's hybrid evidence selection; use --mode hybrid (the default)")
        return 2
    generation = {"backend": "llm"}
    evidence = {}
    if args.top_k is not None:
        if args.top_k < 1:
            logger.error("--top-k must be a positive integer")
            return 2
        evidence["top_k"] = args.top_k
        generation["context_max_items"] = args.top_k
    settings = settings.model_copy(update={
        "generation": settings.generation.model_copy(update=generation),
        "evidence": settings.evidence.model_copy(update=evidence)})
    try:
        generator = make_generator(settings, llm_client_factory(settings.llm))  # fails fast on configuration
    except LLMConfigurationError as exc:
        logger.error("LLM not usable: %s", exc)
        return 2
    return _answer(args, settings, logger, embedder_factory, generator)


def _answer(args, settings, logger, embedder_factory, generator=None) -> int:
    if generator is None and (args.mode != "hybrid" or args.top_k is not None):
        logger.error("--answer always uses hybrid retrieval with evidence.* settings; drop --mode/--top-k")
        return 2
    try:
        pipeline = AnswerPipeline.load(processed_root=settings.paths.data_processed_dir,
                                       indexes_root=settings.paths.indexes_dir, corpus_id=CURATED_CORPUS_ID,
                                       settings=settings, embedder=embedder_factory(settings.embedding),
                                       generator=generator)
        answer = pipeline.answer(args.query)
    except (QueryError, RetrievalIndexError, GroundingError) as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — embedding model could not be loaded
        logger.error("%s: %s", type(exc).__name__, exc)
        return 2
    print(answer.model_dump_json(indent=2) if args.json else render_text(answer, show_evidence=args.show_evidence, show_author=settings.generation.show_opinion_author,
                                                                       show_validation=args.show_validation))
    return 0


if __name__ == "__main__":
    sys.exit(main())
