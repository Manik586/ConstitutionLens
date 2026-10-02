"""Build the Phase 3 indexes for the curated corpus: BM25, embeddings, FAISS.

    python scripts/build_indexes.py                # BM25 + dense (embeddings + FAISS)
    python scripts/build_indexes.py --only bm25    # BM25 only (no model needed)
    python scripts/build_indexes.py --only dense   # embeddings (cached) + FAISS

Reads data/processed/<corpus>/chunks.jsonl (run scripts/chunk_corpus.py first) and
writes indexes/<corpus>/{bm25,dense}/ (D17 - D19). Embeddings of unchanged chunks
are reused from the previous build. The first dense build downloads the embedding
model (configs/v1.yaml: embedding.model_name).

Exit codes: 0 = built, 2 = configuration error, missing chunks, or model unavailable.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from constitutional_evidence_rag.chunking.legal_chunker import CHUNKS_FILENAME, load_chunks
from constitutional_evidence_rag.common.config import ConfigError, load_settings
from constitutional_evidence_rag.common.logging import configure_logging, get_logger
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.ingestion.pipeline import corpus_dir
from constitutional_evidence_rag.retrieval.bm25 import BM25Index
from constitutional_evidence_rag.retrieval.dense import build_dense_index, make_embedder
from constitutional_evidence_rag.retrieval.store import BM25_KIND, DENSE_KIND, RetrievalIndexError, index_dir


def main(argv: list[str] | None = None, embedder_factory=make_embedder) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=None, help="config YAML (default: configs/v1.yaml)")
    parser.add_argument("--only", choices=[BM25_KIND, DENSE_KIND], default=None, help="build one index only")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.logging.level, settings.logging.format)
    logger = get_logger("build_indexes")

    chunks_path = corpus_dir(settings.paths.data_processed_dir, CURATED_CORPUS_ID) / CHUNKS_FILENAME
    if not chunks_path.exists():
        logger.error("No chunks at %s — run scripts/ingest_corpus.py and scripts/chunk_corpus.py first", chunks_path)
        return 2
    chunks = load_chunks(chunks_path)
    indexes_root = settings.paths.indexes_dir
    print(f"{len(chunks)} chunks from {chunks_path}")

    try:
        if args.only in (None, BM25_KIND):
            target = index_dir(indexes_root, CURATED_CORPUS_ID, BM25_KIND)
            bm25 = BM25Index.build(chunks)
            bm25.save(target)
            print(f"bm25   {len(chunks)} chunks, vocabulary {bm25.vocab_size} terms -> {target}")
        if args.only in (None, DENSE_KIND):
            try:
                embedder = embedder_factory(settings.embedding)
            except Exception as exc:  # noqa: BLE001 — ImportError, download/network errors, bad model name
                logger.error("Cannot load embedding model %r: %s: %s", settings.embedding.model_name, type(exc).__name__, exc)
                return 2
            target = index_dir(indexes_root, CURATED_CORPUS_ID, DENSE_KIND)
            report = build_dense_index(chunks, target, embedder)
            print(f"dense  {report.n_chunks} vectors x {report.dimension} ({report.model_name}); "
                  f"{report.computed} embedded, {report.reused} reused from cache -> {target}")
    except RetrievalIndexError as exc:
        logger.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
