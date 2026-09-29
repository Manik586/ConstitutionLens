"""Source registration: manifest, stable document IDs, version registry
(SRS Section 21.1 FR-01, Section 24 `documents`, NFR-07).

* Manifest (D8): a YAML file listing each PDF with the metadata a PDF
  cannot reliably tell us itself (title, source type, canonical source
  URL, source date). Kept source-agnostic per D7 — it is just a list of
  local files, with no assumption about where they were obtained.
* Document IDs (D9): derived from (source_type, normalized source_url),
  so the same source keeps the same ID across re-ingestion and across
  machines, while re-ingestion changes only `document_version`.
* Registry (D4, D10): `metadata.jsonl` holds one `DocumentMetadata` row
  per (document_id, document_version). Older versions are retained with
  `replaced_by` pointing at their successor's version key.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterable
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType

DOCUMENT_ID_PREFIXES: dict[SourceType, str] = {
    SourceType.CONSTITUTIONAL_TEXT: "CONST",
    SourceType.AMENDMENT: "AMEND",
    SourceType.JUDGMENT: "JUDG",
}
_ID_HASH_LENGTH = 12  # 48 bits: collision-free in practice at 10^2–10^3 documents


class ManifestError(Exception):
    """The ingestion manifest is missing, malformed, or inconsistent."""


class RegistryError(Exception):
    """metadata.jsonl is malformed or internally inconsistent."""


# --------------------------------------------------------------------------- IDs


def normalize_source_url(url: str) -> str:
    """Normalize a URL just enough that trivially different spellings of
    the same source map to the same document ID.

    Lower-cases scheme and host, drops the fragment and a trailing '/'.
    The path and query are otherwise kept as-is (they can be
    case-sensitive on real servers).
    """
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") if parts.path != "/" else ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def make_document_id(source_type: SourceType, source_url: str) -> str:
    """Stable, deterministic document ID, e.g. 'JUDG-3f9a1c2b7e4d'."""
    key = f"{source_type.value}|{normalize_source_url(source_url)}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:_ID_HASH_LENGTH]
    return f"{DOCUMENT_ID_PREFIXES[source_type]}-{digest}"


def version_key(document_id: str, document_version: int) -> str:
    """The value stored in a superseded row's `replaced_by` field."""
    return f"{document_id}@v{document_version}"


# --------------------------------------------------------------------- manifest


class ManifestEntry(BaseModel):
    """One source PDF to ingest. `file` is relative to the raw data dir."""

    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    source_type: SourceType
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_date: date | None = None

    @field_validator("file")
    @classmethod
    def _relative_path_inside_raw_dir(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("must be a relative path inside the raw data directory (no '..')")
        return path.as_posix()

    @field_validator("source_url")
    @classmethod
    def _url_has_scheme(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if not parts.scheme or not (parts.netloc or parts.path):
            raise ValueError("must be an absolute URL with a scheme, e.g. https://...")
        return value.strip()

    @property
    def document_id(self) -> str:
        return make_document_id(self.source_type, self.source_url)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[ManifestEntry] = Field(default_factory=list)


def load_manifest(path: Path) -> Manifest:
    """Load and validate a manifest; reject duplicate sources up front."""
    if not path.is_file():
        raise ManifestError(f"Manifest not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        manifest = Manifest.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ManifestError(f"Invalid manifest {path}: {exc}") from exc

    seen: dict[str, str] = {}
    for entry in manifest.documents:
        if entry.document_id in seen:
            raise ManifestError(
                f"Manifest {path}: '{entry.file}' and '{seen[entry.document_id]}' resolve to the "
                f"same source ({entry.source_type.value}, {entry.source_url}) — list it once"
            )
        seen[entry.document_id] = entry.file
    return manifest


# --------------------------------------------------------------------- registry


def load_registry(path: Path) -> list[DocumentMetadata]:
    """Read metadata.jsonl. A missing file is an empty registry."""
    if not path.exists():
        return []

    rows: list[DocumentMetadata] = []
    seen: set[tuple[str, int]] = set()
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = DocumentMetadata.model_validate_json(line)
            except ValidationError as exc:
                raise RegistryError(f"{path}:{line_number}: invalid registry row: {exc}") from exc
            key = (row.document_id, row.document_version)
            if key in seen:
                raise RegistryError(f"{path}:{line_number}: duplicate row for {version_key(*key)}")
            seen.add(key)
            rows.append(row)
    return rows


def latest_version(registry: list[DocumentMetadata], document_id: str) -> DocumentMetadata | None:
    candidates = [row for row in registry if row.document_id == document_id]
    return max(candidates, key=lambda row: row.document_version, default=None)


def write_registry(path: Path, registry: list[DocumentMetadata]) -> None:
    """Rewrite metadata.jsonl atomically, sorted by (document_id, version)."""
    ordered = sorted(registry, key=lambda row: (row.document_id, row.document_version))
    write_jsonl_atomic(path, (row.model_dump_json() for row in ordered))


def write_jsonl_atomic(path: Path, lines: Iterable[str]) -> None:
    """Write lines to a temp file in the same directory, then os.replace()
    it over `path`, so readers never observe a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line)
                f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

