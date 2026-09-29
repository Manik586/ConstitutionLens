"""Ingest the PDFs listed in a manifest into data/processed/ (Phase 1).

Per docs/DECISIONS.md D3, this script — not an HTTP endpoint — is the
Phase 1 entry point for FR-01 source registration.

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
from constitutional_evidence_rag.ingestion.pipeline import ingest_corpus

MANIFEST_FILENAME = "manifest.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=None, help="config YAML (default: configs/v1.yaml)")
    parser.add_argument(
        "--manifest", type=Path, default=None, help=f"manifest YAML (default: <data_raw_dir>/{MANIFEST_FILENAME})"
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.logging.level, settings.logging.format)
    logger = get_logger("ingest_corpus")

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
        f"{len(report.failed)} failed -> {settings.paths.data_processed_dir}"
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
