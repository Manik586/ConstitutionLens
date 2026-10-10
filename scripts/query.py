"""Query the curated corpus: retrieval (Phase 3) or grounded answers (Phase 4; no UI needed).

    python scripts/query.py "What does Article 21 provide?"
    python scripts/query.py "Article 19(2)" --mode bm25 --top-k 5
    python scripts/query.py "basic structure doctrine" --json
    python scripts/query.py "What is Article 21?" --answer [--show-evidence] [--json]
    python scripts/query.py "What protections are provided for personal liberty?" --mode hybrid --top-k 5 --generate
    python scripts/query.py "What does the corpus say about X?" --corpus-id my-research --mode hybrid --top-k 5 --generate

--corpus-id (Phase 7) selects the corpus; default: corpus.default_corpus_id (the curated corpus).
A user corpus must be READY; otherwise a one-line message is printed on stderr (exit 2).

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
import traceback
import json
import sys
from pathlib import Path

from constitutional_evidence_rag.common.config import ConfigError, load_settings
from constitutional_evidence_rag.common.logging import configure_logging, get_logger
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.corpora.errors import CorpusError
from constitutional_evidence_rag.corpora.registry import validate_corpus_id
from constitutional_evidence_rag.corpora.service import CorpusService
from constitutional_evidence_rag.ingestion.metadata import load_registry
from constitutional_evidence_rag.ingestion.pipeline import METADATA_FILENAME
from constitutional_evidence_rag.generation.answer import render_text
from constitutional_evidence_rag.generation.grounding import GroundingError
from constitutional_evidence_rag.generation.llm import LLMConfigurationError, make_llm_client
from constitutional_evidence_rag.generation.pipeline import AnswerPipeline, make_generator
from constitutional_evidence_rag.retrieval.dense import make_embedder
from constitutional_evidence_rag.retrieval.hybrid import Retriever
from constitutional_evidence_rag.retrieval.store import QueryError, RetrievalIndexError


def _describe(c, titles: dict | None = None) -> str:
    where = c.case_name or (f"Article {c.article_number} — {c.article_title}" if c.article_number else None) \
        or c.heading or c.part or (titles or {}).get(c.document_id) or c.document_id
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
    parser.add_argument("--corpus-id", default=None, help="corpus to query (default: corpus.default_corpus_id)")
    parser.add_argument("--verbose", action="store_true", help="print a traceback on errors")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.logging.level, settings.logging.format)
    logger = get_logger("query")
    try:
        args.corpus_id = validate_corpus_id(args.corpus_id or settings.corpus.default_corpus_id)
    except CorpusError as exc:
        return _fail(exc, args)
    if args.corpus_id != CURATED_CORPUS_ID:
        return _user_corpus(args, settings, embedder_factory, llm_client_factory)
    if args.generate:
        return _generate(args, settings, logger, embedder_factory, llm_client_factory)
    if args.answer:
        return _answer(args, settings, logger, embedder_factory)

    try:
        embedder = embedder_factory(settings.embedding) if args.mode != "bm25" else None
        retriever = Retriever.load(processed_root=settings.paths.data_processed_dir,
                                   indexes_root=settings.paths.indexes_dir, corpus_id=args.corpus_id,
                                   settings=settings, embedder=embedder)
        results = getattr(retriever, args.mode)(args.query, args.top_k)
    except (QueryError, RetrievalIndexError) as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — embedding model could not be loaded
        logger.error("%s: %s", type(exc).__name__, exc)
        return 2

    return _print_results(results, args)


def _print_results(results, args, titles: dict | None = None) -> int:
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
        print(f"{r.rank:>2}. [{' | '.join(parts)}] {r.chunk_id}\n    {_describe(r.chunk, titles)}\n    {snippet}…")
    return 0


def _fail(exc: BaseException, args) -> int:
    """One clean line on stderr (Phase 7); the traceback only with --verbose."""
    message = getattr(exc, "message", None) or str(exc)
    print(f"error: {message}", file=sys.stderr)
    if getattr(args, "verbose", False):
        traceback.print_exc()
    return 2


def _user_corpus(args, settings, embedder_factory, llm_client_factory) -> int:
    """Phase 7: query a user corpus through the corpus service (status checked first)."""
    service = CorpusService(settings, embedder_factory=embedder_factory, llm_client_factory=llm_client_factory)
    try:
        service.registry.ensure_queryable(args.corpus_id)  # before any index is touched
        if args.generate or args.answer:
            answer = service.query_corpus(args.corpus_id, args.query, mode=args.mode, top_k=args.top_k, generate=args.generate)
            print(answer.model_dump_json(indent=2) if args.json else render_text(
                answer, show_evidence=args.show_evidence, show_author=settings.generation.show_opinion_author,
                show_validation=args.show_validation))
            return 0
        embedder = service.embedder() if args.mode != "bm25" else None
        retriever = Retriever.load(processed_root=settings.paths.data_processed_dir, indexes_root=settings.paths.indexes_dir,
                                   corpus_id=args.corpus_id, settings=settings, embedder=embedder)
        results = getattr(retriever, args.mode)(args.query, args.top_k)
        metadata = service.registry.processed_dir(args.corpus_id) / METADATA_FILENAME
        titles = {m.document_id: m.title for m in load_registry(metadata)} if metadata.exists() else {}
        return _print_results(results, args, titles)
    except (CorpusError, QueryError, RetrievalIndexError, GroundingError, LLMConfigurationError) as exc:
        return _fail(exc, args)
    except Exception as exc:  # noqa: BLE001 — e.g. the embedding model could not be loaded
        return _fail(RuntimeError(f"{type(exc).__name__}: {exc}"), args)


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
                                       indexes_root=settings.paths.indexes_dir, corpus_id=args.corpus_id,
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
