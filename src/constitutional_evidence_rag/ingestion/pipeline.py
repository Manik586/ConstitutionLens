"""Phase 1 ingestion pipeline (SRS Section 21.1 FR-01/FR-02, NFR-03,
NFR-07; docs/DECISIONS.md D3, D4, D10, D12).

    manifest.yaml ──► for each entry:
                        derive stable document_id
                        hash the PDF; skip if unchanged since latest version
                        parse page-by-page (pdf_parser)
                        assign next document_version
                  ──► data/processed/documents.jsonl  (one ParsedDocument per line)
                  ──► data/processed/metadata.jsonl   (DocumentMetadata registry)

A failure in one document is recorded in the report and does not stop
the others. Output is written only after all entries are processed, and
each file is replaced atomically.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from constitutional_evidence_rag.common.logging import get_logger
from constitutional_evidence_rag.common.models import DocumentMetadata
from constitutional_evidence_rag.common.pages import ParsedDocument
from constitutional_evidence_rag.ingestion.metadata import (
    ManifestEntry,
    RegistryError,
    latest_version,
    load_manifest,
    load_registry,
    version_key,
    write_jsonl_atomic,
    write_registry,
)
from constitutional_evidence_rag.ingestion.pdf_parser import (
    MissingPDFError,
    PDFIngestionError,
    parse_pdf,
)

logger = get_logger(__name__)

DOCUMENTS_FILENAME = "documents.jsonl"
METADATA_FILENAME = "metadata.jsonl"


class IngestionStatus(str, Enum):
    INGESTED = "ingested"  # new document, or new version of an existing one
    UNCHANGED = "unchanged"  # same bytes and metadata as the latest version; nothing written
    FAILED = "failed"


@dataclass(frozen=True)
class DocumentResult:
    file: str
    document_id: str
    status: IngestionStatus
    document_version: int | None = None
    page_count: int | None = None
    empty_pages: tuple[int, ...] = ()
    error: str | None = None


@dataclass
class IngestionReport:
    results: list[DocumentResult] = field(default_factory=list)

    def _with(self, status: IngestionStatus) -> list[DocumentResult]:
        return [r for r in self.results if r.status is status]

    @property
    def ingested(self) -> list[DocumentResult]:
        return self._with(IngestionStatus.INGESTED)

    @property
    def unchanged(self) -> list[DocumentResult]:
        return self._with(IngestionStatus.UNCHANGED)

    @property
    def failed(self) -> list[DocumentResult]:
        return self._with(IngestionStatus.FAILED)


def ingest_corpus(
    manifest_path: Path,
    raw_dir: Path,
    processed_dir: Path,
    *,
    ingestion_date: datetime | None = None,
) -> IngestionReport:
    """Ingest every PDF listed in `manifest_path` (paths relative to `raw_dir`).

    Raises ManifestError / RegistryError for problems with the manifest or
    existing output files (nothing is written in that case). Per-document
    problems (missing/unreadable/empty PDFs) are reported, not raised.
    """
    manifest = load_manifest(manifest_path)
    documents_path = processed_dir / DOCUMENTS_FILENAME
    metadata_path = processed_dir / METADATA_FILENAME

    registry = load_registry(metadata_path)
    registered = {(row.document_id, row.document_version) for row in registry}
    existing_rows = _load_document_rows(documents_path, keep=registered)
    known_hashes = {key: json.loads(line)["file_sha256"] for key, line in existing_rows}

    now = ingestion_date or datetime.now(timezone.utc)
    report = IngestionReport()
    new_documents: list[ParsedDocument] = []

    for entry in manifest.documents:
        document_id = entry.document_id
        pdf_path = raw_dir / entry.file
        try:
            if not pdf_path.is_file():
                raise MissingPDFError(f"PDF not found: {pdf_path}")
            file_sha256 = _sha256(pdf_path)

            previous = latest_version(registry, document_id)
            if previous is not None and _is_unchanged(previous, entry, file_sha256, known_hashes):
                logger.info("Unchanged, skipping: %s (%s@v%d)", entry.file, document_id,
                            previous.document_version)
                report.results.append(DocumentResult(
                    entry.file, document_id, IngestionStatus.UNCHANGED, previous.document_version))
                continue

            metadata = DocumentMetadata(
                document_id=document_id,
                source_type=entry.source_type,
                title=entry.title,
                source_url=entry.source_url,
                document_version=(previous.document_version + 1) if previous else 1,
                source_date=entry.source_date,
                ingestion_date=now,
            )
            pages = parse_pdf(pdf_path, metadata)
            parsed = ParsedDocument(
                document_id=document_id,
                document_version=metadata.document_version,
                source_type=entry.source_type,
                source_file=entry.file,
                file_sha256=file_sha256,
                page_count=len(pages),
                pages=pages,
            )
        except PDFIngestionError as exc:
            logger.error("Failed to ingest %s: %s", entry.file, exc)
            report.results.append(DocumentResult(
                entry.file, document_id, IngestionStatus.FAILED, error=f"{type(exc).__name__}: {exc}"))
            continue

        # Only commit registry changes once parsing has fully succeeded.
        if previous is not None:
            registry[registry.index(previous)] = previous.model_copy(
                update={"replaced_by": version_key(document_id, metadata.document_version)}
            )
        registry.append(metadata)
        new_documents.append(parsed)
        report.results.append(DocumentResult(
            entry.file, document_id, IngestionStatus.INGESTED, metadata.document_version,
            parsed.page_count, tuple(parsed.empty_page_numbers)))
        logger.info("Ingested %s as %s@v%d (%d pages)", entry.file, document_id,
                    metadata.document_version, parsed.page_count)

    if new_documents:
        write_jsonl_atomic(
            documents_path,
            [line for _, line in existing_rows] + [d.model_dump_json() for d in new_documents],
        )
        write_registry(metadata_path, registry)

    logger.info("Ingestion complete: %d ingested, %d unchanged, %d failed",
                len(report.ingested), len(report.unchanged), len(report.failed))
    return report


def load_parsed_documents(path: Path) -> list[ParsedDocument]:
    """Read and fully validate documents.jsonl (for Phase 2 chunking and tests)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [ParsedDocument.model_validate_json(line) for line in f if line.strip()]


# ---------------------------------------------------------------------- helpers


def _is_unchanged(
    previous: DocumentMetadata,
    entry: ManifestEntry,
    file_sha256: str,
    known_hashes: dict[tuple[str, int], str],
) -> bool:
    return (
        known_hashes.get((previous.document_id, previous.document_version)) == file_sha256
        and previous.title == entry.title
        and previous.source_date == entry.source_date
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_document_rows(
    path: Path, keep: set[tuple[str, int]]
) -> list[tuple[tuple[str, int], str]]:
    """Existing documents.jsonl rows as ((document_id, version), raw_line).

    Rows for versions absent from the registry are dropped: they can only
    come from a run interrupted between writing documents.jsonl and
    metadata.jsonl, and keeping them would let a later run reuse that
    version number for different content.
    """
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                key = (obj["document_id"], int(obj["document_version"]))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise RegistryError(f"{path}:{line_number}: unreadable row: {exc}") from exc
            if key in keep:
                rows.append((key, line.rstrip("\n")))
            else:
                logger.warning("Dropping orphaned %s@v%d from %s (not in registry)", *key, path.name)
    return rows
