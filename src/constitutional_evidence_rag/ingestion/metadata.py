"""Source registration: manifest, stable document IDs, version registry
(SRS Section 21.1 FR-01, Section 24 `documents`, NFR-07).

* DocumentSpec (D15): the caller-supplied metadata for one document
  (source type, title, canonical source URL, source date). It is the
  generic input to ingestion, independent of where the request came from.
* Manifest (D8): the V1 way of supplying DocumentSpecs — a YAML file that
  defines the curated corpus, one ManifestEntry (= DocumentSpec + file
  path) per PDF. V2 user uploads will supply DocumentSpecs another way
  and reuse everything else unchanged.
* Document IDs (D9, D15): derived from (corpus_id, source_type,
  normalized source_url), so the same source keeps the same ID across
  re-ingestion and machines, and the same source in two corpora never
  collides.
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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType

DOCUMENT_ID_PREFIXES: dict[SourceType, str] = {
    SourceType.CONSTITUTIONAL_TEXT: "CONST",
    SourceType.AMENDMENT: "AMEND",
    SourceType.JUDGMENT: "JUDG",
}
_ID_HASH_LENGTH = 12  # 48 bits: collision-free in practice at 10^2–10^3 documents

# Source types whose date is mandatory in the manifest (D14): a judgment's
# date and the constitution text's "as on" date are what keep a 1973
# holding distinguishable from the 2026 text it sits next to.
DATE_REQUIRED_SOURCE_TYPES: frozenset[SourceType] = frozenset(
    {SourceType.JUDGMENT, SourceType.CONSTITUTIONAL_TEXT}
)


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


def make_document_id(corpus_id: str, source_type: SourceType, source_url: str) -> str:
    """Stable, deterministic document ID, e.g. 'JUDG-3f9a1c2b7e4d'.

    Scoped to the corpus (D15): the same judgment in the curated corpus and
    in a V2 user collection gets two different IDs, so citations, indices,
    and registries never mix documents across corpora.
    """
    key = f"{corpus_id}|{source_type.value}|{normalize_source_url(source_url)}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:_ID_HASH_LENGTH]
    return f"{DOCUMENT_ID_PREFIXES[source_type]}-{digest}"


def version_key(document_id: str, document_version: int) -> str:
    """The value stored in a superseded row's `replaced_by` field."""
    return f"{document_id}@v{document_version}"


# ------------------------------------------------------- document spec / manifest


class DocumentSpec(BaseModel):
    """Caller-supplied metadata for one document to ingest (D15).

    This is the generic ingestion input: the V1 manifest provides it for the
    curated corpus; V2 uploads will provide it from the upload request.
    `source_date` is the judgment date for judgments and the "as on" date of
    the text for the constitution; both are required (D14). It stays optional
    on `DocumentMetadata`, which mirrors SRS Section 24.
    """

    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_date: date | None = None

    @field_validator("source_url")
    @classmethod
    def _url_has_scheme(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if not parts.scheme or not (parts.netloc or parts.path):
            raise ValueError("must be an absolute URL with a scheme, e.g. https://...")
        return value.strip()

    @model_validator(mode="after")
    def _date_required_for_dated_sources(self) -> DocumentSpec:
        if self.source_type in DATE_REQUIRED_SOURCE_TYPES and self.source_date is None:
            what = "judgment date" if self.source_type is SourceType.JUDGMENT else '"as on" date of the text'
            raise ValueError(
                f"source_date is required for {self.source_type.value} "
                f"(the {what}, YYYY-MM-DD) — see docs/DECISIONS.md D14"
            )
        return self

    @property
    def source_key(self) -> tuple[SourceType, str]:
        """What makes two specs the same source within one corpus."""
        return (self.source_type, normalize_source_url(self.source_url))


class ManifestEntry(DocumentSpec):
    """V1 curated-corpus manifest row: a DocumentSpec plus the PDF's path,
    relative to the raw data directory."""

    file: str = Field(min_length=1)

    @field_validator("file")
    @classmethod
    def _relative_path_inside_raw_dir(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("must be a relative path inside the raw data directory (no '..')")
        return path.as_posix()

    @property
    def spec(self) -> DocumentSpec:
        return DocumentSpec.model_validate(self.model_dump(exclude={"file"}))


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[ManifestEntry] = Field(default_factory=list)


def load_manifest(path: Path) -> Manifest:
    """Load and validate a manifest; reject duplicate sources up front.

    Validation errors name the offending entry's `file`, not just its index.
    """
    if not path.is_file():
        raise ManifestError(f"Manifest not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid manifest {path}: {exc}") from exc
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(f"Invalid manifest {path}:\n{_describe_errors(exc, raw)}") from exc

    seen: dict[tuple[SourceType, str], str] = {}
    for entry in manifest.documents:
        if entry.source_key in seen:
            raise ManifestError(
                f"Manifest {path}: '{entry.file}' and '{seen[entry.source_key]}' resolve to the "
                f"same source ({entry.source_type.value}, {entry.source_url}) — list it once"
            )
        seen[entry.source_key] = entry.file
    return manifest


def _describe_errors(exc: ValidationError, raw: object) -> str:
    entries = raw.get("documents") if isinstance(raw, dict) else None
    lines = []
    for err in exc.errors():
        loc = err["loc"]
        where = ".".join(str(part) for part in loc)
        if len(loc) >= 2 and loc[0] == "documents" and isinstance(loc[1], int) and isinstance(entries, list):
            item = entries[loc[1]] if loc[1] < len(entries) else None
            file = item.get("file") if isinstance(item, dict) else None
            field = ".".join(str(part) for part in loc[2:]) or "entry"
            where = f"entry '{file}' ({field})" if file else f"entry #{loc[1] + 1} ({field})"
        lines.append(f"  - {where}: {err['msg']}")
    return "\n".join(lines)


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
    it over `path`, so readers never observe a half-written file.

    mkstemp() creates files as 0600; the result is given the permissions a
    normal write would have (the existing file's mode, else 0666 & ~umask),
    so output written from a root container is still readable on the host.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line)
                f.write("\n")
        os.chmod(tmp_name, _target_mode(path))
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _target_mode(path: Path) -> int:
    if path.exists():
        return path.stat().st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask
