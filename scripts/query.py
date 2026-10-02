"""Run a retrieval query against the curated corpus indexes (Phase 3; no UI needed).

    python scripts/query.py "What does Article 21 provide?"
    python scripts/query.py "Article 19(2)" --mode bm25 --top-k 5
    python scripts/query.py "basic structure doctrine" --json

Modes: hybrid (default; BM25 + dense fused by RRF), bm25, dense. Results carry full
provenance (document, version, pages, article/heading, opinion author, source URL).
No reranking yet — that is Phase 4.

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
from constitutional_evidence_rag.retrieval.dense import make_embedder
from constitutional_evidence_rag.retrieval.hybrid import Retriever
from constitutional_evidence_rag.retrieval.store import QueryError, RetrievalIndexError


def _describe(c) -> str:
    where = c.case_name or (f"Article {c.article_number} — {c.article_title}" if c.article_number else None) \
        or c.heading or c.part or c.document_id
    extra = f" | {c.opinion_author}" if c.opinion_author else ""
    return f"{where}{extra} | pp. {c.page_start}-{c.page_end}"


def main(argv: list[str] | None = None, embedder_factory=make_embedder) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query")
    parser.add_argument("--mode", choices=["hybrid", "bm25", "dense"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=None, help="results to return (default: config top_n for the mode)")
    parser.add_argument("--json", action="store_true", help="print RetrievedChunk JSON lines")
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


if __name__ == "__main__":
    sys.exit(main())
