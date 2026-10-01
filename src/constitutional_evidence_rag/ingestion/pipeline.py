"""Phase 1 ingestion pipeline (SRS Section 21.1 FR-01/FR-02, NFR-03,
NFR-07; docs/DECISIONS.md D3, D4, D10, D12, D15).

Generic core (D15) — knows nothing about *which* documents it ingests:

    ingest_documents([(pdf_path, DocumentSpec), ...], corpus_id=..., ...)
    ingest_document(pdf_path, DocumentSpec, corpus_id=..., ...)   # one file
        for each document:
            derive corpus-scoped document_id
            hash the PDF; skip if unchanged since latest version
            parse page-by-page (pdf_parser)
            assign next document_version
        ──► <processed_root>/<corpus_id>/documents.jsonl  (ParsedDocument per line)
        ──► <processed_root>/<corpus_id>/metadata.jsonl   (DocumentMetadata registry)

V1 adapter — the only V1 caller:

    ingest_corpus(manifest.yaml, raw_dir, processed_root)  # curated corpus

Each corpus has its own directory, so ingesting into one corpus can never
rewrite another's files. That is what keeps the V1 curated snapshot fixed
(NFR-04) once V2 adds user collections through this same core.

A failure in one document is recorded in the report and does not stop the
others. Output is written only after all documents are processed, and each
file is replaced atomically.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import TypeAdapter

from constitutional_evidence_rag.common.logging import get_logger
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, CorpusId, DocumentMetadata
from constitutional_evidence_rag.common.pages import ParsedDocument
from constitutional_evidence_rag.ingestion.metadata import (
    DocumentSpec,
    RegistryError,
    latest_version,
    load_manifest,
    load_registry,
    make_document_id,
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

_CORPUS_ID = TypeAdapter(CorpusId)


def corpus_dir(processed_root: Path, corpus_id: str) -> Path:
    """Storage directory for one corpus. Validates corpus_id first, since it
    becomes a path component."""
    return processed_root / _CORPUS_ID.validate_python(corpus_id)


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
    processed_root: Path,
    *,
    corpus_id: str = CURATED_CORPUS_ID,
    ingestion_date: datetime | None = None,
) -> IngestionReport:
    """V1 adapter: ingest the curated corpus defined by a manifest.

    Manifest paths are relative to `raw_dir`. Raises ManifestError for an
    invalid manifest and RegistryError for unreadable existing output
    (nothing is written in either case).
    """
    manifest = load_manifest(manifest_path)
    return ingest_documents(
        [(raw_dir / entry.file, entry.spec) for entry in manifest.documents],
        corpus_id=corpus_id,
        processed_root=processed_root,
        base_dir=raw_dir,
        ingestion_date=ingestion_date,
    )


def ingest_document(
    document_path: Path,
    spec: DocumentSpec,
    *,
    corpus_id: str,
    processed_root: Path,
    ingestion_date: datetime | None = None,
) -> DocumentResult:
    """Ingest one PDF into `corpus_id` — the generic single-document entry
    point (conceptually `ingest(document_path, corpus_id)`).

    Not called by any V1 code path; it exists so V2 uploads reuse the
    pipeline rather than re-implementing it (D15).
    """
    report = ingest_documents(
        [(document_path, spec)],
        corpus_id=corpus_id,
        processed_root=processed_root,
        base_dir=document_path.parent,
        ingestion_date=ingestion_date,
    )
    return report.results[0]


def ingest_documents(
    documents: Sequence[tuple[Path, DocumentSpec]],
    *,
    corpus_id: str,
    processed_root: Path,
    base_dir: Path,
    ingestion_date: datetime | None = None,
) -> IngestionReport:
    """Generic core: ingest PDFs into one corpus's storage.

    `source_file` in the output is each path relative to `base_dir`.
    Per-document problems (missing/unreadable/empty PDFs) are reported, not
    raised. Raises ValueError for an invalid corpus_id or for the same source
    listed twice in one call.
    """
    target = corpus_dir(processed_root, corpus_id)
    keys = [spec.source_key for _, spec in documents]
    if len(set(keys)) != len(keys):
        raise ValueError(f"the same source appears more than once in one ingestion call for {corpus_id}")

    documents_path = target / DOCUMENTS_FILENAME
    metadata_path = target / METADATA_FILENAME

    registry = load_registry(metadata_path)
    registered = {(row.document_id, row.document_version) for row in registry}
    existing_rows = _load_document_rows(documents_path, keep=registered)
    known_hashes = {key: json.loads(line)["file_sha256"] for key, line in existing_rows}

    now = ingestion_date or datetime.now(timezone.utc)
    report = IngestionReport()
    new_documents: list[ParsedDocument] = []

    for pdf_path, spec in documents:
        source_file = pdf_path.relative_to(base_dir).as_posix()
        document_id = make_document_id(corpus_id, spec.source_type, spec.source_url)
        try:
            if not pdf_path.is_file():
                raise MissingPDFError(f"PDF not found: {pdf_path}")
            file_sha256 = _sha256(pdf_path)

            previous = latest_version(registry, document_id)
            if previous is not None and _is_unchanged(previous, spec, file_sha256, known_hashes):
                logger.info("Unchanged, skipping: %s (%s/%s@v%d)", source_file, corpus_id, document_id,
                            previous.document_version)
                report.results.append(DocumentResult(
                    source_file, document_id, IngestionStatus.UNCHANGED, previous.document_version))
                continue

            metadata = DocumentMetadata(
                document_id=document_id,
                corpus_id=corpus_id,
                source_type=spec.source_type,
                title=spec.title,
                source_url=spec.source_url,
                document_version=(previous.document_version + 1) if previous else 1,
                source_date=spec.source_date,
                ingestion_date=now,
            )
            pages = parse_pdf(pdf_path, metadata)
            parsed = ParsedDocument(
                corpus_id=corpus_id,
                document_id=document_id,
                document_version=metadata.document_version,
                source_type=spec.source_type,
                source_file=source_file,
                file_sha256=file_sha256,
                page_count=len(pages),
                pages=pages,
            )
        except PDFIngestionError as exc:
            logger.error("Failed to ingest %s: %s", source_file, exc)
            report.results.append(DocumentResult(
                source_file, document_id, IngestionStatus.FAILED, error=f"{type(exc).__name__}: {exc}"))
            continue

        # Only commit registry changes once parsing has fully succeeded.
        if previous is not None:
            registry[registry.index(previous)] = previous.model_copy(
                update={"replaced_by": version_key(document_id, metadata.document_version)}
            )
        registry.append(metadata)
        new_documents.append(parsed)
        report.results.append(DocumentResult(
            source_file, document_id, IngestionStatus.INGESTED, metadata.document_version,
            parsed.page_count, tuple(parsed.empty_page_numbers)))
        logger.info("Ingested %s as %s/%s@v%d (%d pages)", source_file, corpus_id, document_id,
                    metadata.document_version, parsed.page_count)

    if new_documents:
        write_jsonl_atomic(
            documents_path,
            [line for _, line in existing_rows] + [d.model_dump_json() for d in new_documents],
        )
        write_registry(metadata_path, registry)

    logger.info("Ingestion into %s complete: %d ingested, %d unchanged, %d failed", corpus_id,
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
    spec: DocumentSpec,
    file_sha256: str,
    known_hashes: dict[tuple[str, int], str],
) -> bool:
    return (
        known_hashes.get((previous.document_id, previous.document_version)) == file_sha256
        and previous.title == spec.title
        and previous.source_date == spec.source_date
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
