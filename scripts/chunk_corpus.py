"""Chunk the ingested curated V1 corpus into data/processed/<corpus>/chunks.jsonl (Phase 2).

Run after scripts/ingest_corpus.py. Like ingestion, this always targets the
curated corpus (docs/DECISIONS.md D15, D16).

    python scripts/chunk_corpus.py [--config configs/v1.yaml]

Exit codes: 0 = chunks written, 2 = configuration error or no ingested corpus.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from constitutional_evidence_rag.chunking.legal_chunker import CHUNKS_FILENAME, ChunkingError, chunk_corpus
from constitutional_evidence_rag.common.config import ConfigError, load_settings
from constitutional_evidence_rag.common.logging import configure_logging, get_logger
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.corpora.errors import CorpusError
from constitutional_evidence_rag.corpora.registry import validate_corpus_id
from constitutional_evidence_rag.corpora.service import CorpusService
from constitutional_evidence_rag.ingestion.metadata import RegistryError
from constitutional_evidence_rag.ingestion.pipeline import corpus_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=None, help="config YAML (default: configs/v1.yaml)")
    parser.add_argument("--corpus-id", default=None, help="Phase 7: corpus to chunk (default: corpus.default_corpus_id)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.logging.level, settings.logging.format)
    logger = get_logger("chunk_corpus")

    processed = settings.paths.data_processed_dir
    try:
        corpus_id = validate_corpus_id(args.corpus_id or settings.corpus.default_corpus_id)
        if corpus_id != CURATED_CORPUS_ID:
            report = CorpusService(settings).chunk(corpus_id)  # updates corpus.json status
        else:
            report = chunk_corpus(processed, corpus_id, settings.chunking)
    except CorpusError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    except (ChunkingError, RegistryError) as exc:
        logger.error("%s", exc)
        return 2

    for document_id, count in report.per_document.items():
        print(f"{document_id:<18} {count:>5} chunks")
    methods = ", ".join(f"{n} {m}" for m, n in sorted(report.methods.items()))
    print(f"\n{report.documents} documents, {report.chunks} chunks ({methods or 'none'}) -> "
          f"{corpus_dir(processed, corpus_id) / CHUNKS_FILENAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
