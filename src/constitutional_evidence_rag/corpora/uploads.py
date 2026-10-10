"""Upload validation and storage for user corpora (Phase 7; D27).

Per file, in order (the first failure rejects that file only):
  extension allowed · content starts with %PDF- · size <= max_file_size_mb ·
  corpus document count and total size within limits · opens as an unencrypted PDF ·
  at least one page · pages <= max_pages_per_document · some extractable text
  (image-only scans are rejected: no OCR for uploads yet).
Identical bytes already in the corpus -> "unchanged" (nothing stored twice).

Storage never uses the client filename as a path: files are stored as
data/uploads/<corpus_id>/<sha256[:12]>.pdf; the sanitized original name is kept only as the
display title (what citations show). uploads.json records stored files, written atomically.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from constitutional_evidence_rag.common.config import UploadSettings

UPLOADS_INDEX = "uploads.json"
SCANNED_REASON = "No extractable text (scanned PDFs are not supported yet)"


@dataclass(frozen=True)
class FileResult:
    filename: str  # sanitized display name
    status: str  # "accepted" | "unchanged" | "rejected"
    reason: str | None = None
    pages: int | None = None
    size_bytes: int = 0
    stored_as: str | None = None  # storage name (no directory)

    def to_dict(self) -> dict:
        return asdict(self)


def sanitize_filename(name: str | None) -> str:
    """Display title from a client-supplied name: last path component only, no control or
    reserved characters, bounded length. Never used as a filesystem path."""
    base = re.split(r"[\\\\/]", name or "")[-1]
    base = unicodedata.normalize("NFKC", base)
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*').strip().strip(".")
    if not base:
        return "document.pdf"
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = stem[:120].strip() or "document"
    return f"{stem}.{ext[:10]}" if ext else stem


def inspect_pdf(data: bytes, limits: UploadSettings) -> tuple[int | None, str | None]:
    """(page_count, rejection_reason)."""
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001 — any parse failure means unreadable
        return None, "The file is not a readable PDF (it may be corrupt)."
    try:
        if document.needs_pass or document.is_encrypted:
            return None, "The PDF is password-protected."
        pages = document.page_count
        if pages == 0:
            return 0, "The PDF has no pages."
        if pages > limits.max_pages_per_document:
            return pages, f"The PDF has {pages} pages; the limit is {limits.max_pages_per_document}."
        try:
            has_text = any(page.get_text().strip() for page in document)
        except Exception:  # noqa: BLE001
            return pages, "The file is not a readable PDF (it may be corrupt)."
        if not has_text:
            return pages, SCANNED_REASON + "."
        return pages, None
    finally:
        document.close()


class UploadStore:
    """Stored uploads for one corpus."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.index_path = directory / UPLOADS_INDEX

    def entries(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def save_entries(self, entries: dict[str, dict]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".uploads.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(entries, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, self.index_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def write_file(self, name: str, data: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".upload.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, self.directory / name)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def add(self, files: list[tuple[str | None, bytes]], limits: UploadSettings) -> list[FileResult]:
        entries = self.entries()
        total = sum(e["size_bytes"] for e in entries.values())
        results = []
        for client_name, data in files:
            title = sanitize_filename(client_name)
            size = len(data)
            ext = Path(title).suffix.lower()
            reject = None
            if ext not in [e.lower() for e in limits.allowed_extensions]:
                reject = f"Only {', '.join(limits.allowed_extensions)} files are accepted."
            elif size == 0:
                reject = "The file is empty."
            elif size > limits.max_file_size_mb * 1024 * 1024:
                reject = f"The file is larger than {limits.max_file_size_mb:g} MB."
            elif not data.startswith(b"%PDF-"):
                reject = "The file is not a PDF (its content does not start with %PDF-)."
            if reject:
                results.append(FileResult(title, "rejected", reject, size_bytes=size))
                continue
            digest = hashlib.sha256(data).hexdigest()
            stored = f"{digest[:12]}.pdf"
            if stored in entries:
                results.append(FileResult(title, "unchanged", "This file is already in the corpus.",
                                          entries[stored]["pages"], size, stored))
                continue
            if len(entries) >= limits.max_documents_per_corpus:
                results.append(FileResult(title, "rejected",
                                          f"The corpus already has the maximum of {limits.max_documents_per_corpus} documents.",
                                          size_bytes=size))
                continue
            if total + size > limits.max_total_size_mb * 1024 * 1024:
                results.append(FileResult(title, "rejected",
                                          f"Adding this file would exceed the corpus limit of {limits.max_total_size_mb:g} MB.",
                                          size_bytes=size))
                continue
            pages, reject = inspect_pdf(data, limits)
            if reject:
                results.append(FileResult(title, "rejected", reject, pages, size))
                continue
            self.write_file(stored, data)
            entries[stored] = {"title": title, "sha256": digest, "size_bytes": size, "pages": pages,
                               "added_at": datetime.now(timezone.utc).isoformat()}
            total += size
            results.append(FileResult(title, "accepted", None, pages, size, stored))
        self.save_entries(entries)
        return results
