"""Ingest the curated V1 corpus listed in a manifest into data/processed/ (Phase 1).

Per docs/DECISIONS.md D3, this script — not an HTTP endpoint — is the
Phase 1 entry point for FR-01 source registration. It always targets the
curated corpus (CURATED_CORPUS_ID); there is deliberately no --corpus
option, because creating other corpora is the V2 user-collection feature
(docs/SRS_v4.2_AMENDMENT.md, D15).

    python scripts/ingest_corpus.py                       # uses configs/v1.yaml
    python scripts/ingest_corpus.py --manifest path.yaml  # explicit manifest

Exit codes: 0 = all documents ingested or unchanged,
            1 = at least one document failed (others were still written),
            2 = configuration / manifest / registry error (nothing written).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from constitutional_evidence_rag.common.config import ConfigError, load_settings
from constitutional_evidence_rag.common.logging import configure_logging, get_logger
from constitutional_evidence_rag.ingestion.metadata import ManifestError, RegistryError
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.corpora.errors import CorpusError
from constitutional_evidence_rag.corpora.registry import validate_corpus_id
from constitutional_evidence_rag.corpora.service import CorpusService
from constitutional_evidence_rag.ingestion.pipeline import corpus_dir, ingest_corpus

MANIFEST_FILENAME = "manifest.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=None, help="config YAML (default: configs/v1.yaml)")
    parser.add_argument(
        "--manifest", type=Path, default=None, help=f"manifest YAML (default: <data_raw_dir>/{MANIFEST_FILENAME})"
    )
    parser.add_argument("--corpus-id", default=None,
                        help="Phase 7: target corpus (default: corpus.default_corpus_id). The curated corpus is ingested "
                             "from its manifest; a user corpus from --input-dir.")
    parser.add_argument("--input-dir", type=Path, default=None,
                        help="Phase 7: ingest every *.pdf in this folder into a user corpus (created if missing)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.logging.level, settings.logging.format)
    logger = get_logger("ingest_corpus")

    try:
        corpus_id = validate_corpus_id(args.corpus_id or settings.corpus.default_corpus_id)
    except CorpusError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    if args.input_dir is not None or corpus_id != CURATED_CORPUS_ID:
        return _ingest_user_corpus(corpus_id, args, settings)

    raw_dir = settings.paths.data_raw_dir
    manifest = args.manifest or raw_dir / MANIFEST_FILENAME
    try:
        report = ingest_corpus(manifest, raw_dir, settings.paths.data_processed_dir)
    except (ManifestError, RegistryError) as exc:
        logger.error("%s", exc)
        return 2

    for result in report.results:
        detail = (
            f"v{result.document_version}, {result.page_count} pages"
            + (f", no text on pages {list(result.empty_pages)}" if result.empty_pages else "")
            if result.page_count
            else (f"v{result.document_version}" if result.document_version else result.error)
        )
        print(f"{result.status.value:<9} {result.document_id:<18} {result.file}  ({detail})")
    print(
        f"\n{len(report.ingested)} ingested, {len(report.unchanged)} unchanged, "
        f"{len(report.failed)} failed -> "
        f"{corpus_dir(settings.paths.data_processed_dir, CURATED_CORPUS_ID)}"
    )
    return 1 if report.failed else 0


def _ingest_user_corpus(corpus_id: str, args, settings) -> int:
    """Phase 7: validate and store every *.pdf in --input-dir as USER_DOCUMENT, then ingest
    (corpus.json status updated as it goes). Rejected files are reported; the rest proceed."""
    if corpus_id == CURATED_CORPUS_ID:
        print(f"error: Corpus '{corpus_id}' is reserved; it is ingested from its manifest, not --input-dir.", file=sys.stderr)
        return 2
    if args.input_dir is None:
        print("error: a user corpus is ingested from a folder of PDFs: pass --input-dir.", file=sys.stderr)
        return 2
    if not args.input_dir.is_dir():
        print(f"error: --input-dir {args.input_dir.name!r} is not a folder.", file=sys.stderr)
        return 2
    service = CorpusService(settings)
    try:
        if not service.registry.exists(corpus_id):
            service.create_corpus(corpus_id)
        _, results = service.add_documents_from_dir(corpus_id, args.input_dir)
        for r in results:
            print(f"{r.status:<9} {r.filename}" + (f"  ({r.reason})" if r.reason else f"  ({r.pages} pages)"))
        report = service.ingest_uploads(corpus_id)
    except CorpusError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    rejected = [r for r in results if r.status == "rejected"]
    print(f"\n{len(report.ingested)} ingested, {len(report.unchanged)} unchanged, {len(report.failed)} failed, "
          f"{len(rejected)} rejected -> corpus '{corpus_id}' (status: {service.get_corpus(corpus_id).status.value})")
    return 1 if rejected or report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
