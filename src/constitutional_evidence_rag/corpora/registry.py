"""Corpus registry (Phase 7; D28).

One `corpus.json` per user corpus at data/processed/<corpus_id>/corpus.json, written
atomically (temp file + os.replace). No database (consistent with D4).

The curated corpus (CURATED_CORPUS_ID) is RESERVED: it cannot be created, uploaded to,
rebuilt or deleted through the user-corpus service/API, and it never gets a corpus.json —
its status is inferred: READY iff chunks.jsonl and both indexes exist, else UPLOADED.
That keeps the curated corpus working with zero migration.

Locking: an in-process lock plus an on-disk lock file per corpus, so two builds of the same
corpus cannot run at once even across processes (CLI and API). Crash recovery
(`recover_stale`) turns corpora left in PROCESSING into FAILED and clears stale lock files.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from constitutional_evidence_rag.chunking.legal_chunker import CHUNKS_FILENAME
from constitutional_evidence_rag.common.config import PathsSettings
from constitutional_evidence_rag.common.logging import get_logger
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, CorpusId
from constitutional_evidence_rag.corpora.errors import (
    CorpusBusyError, CorpusExistsError, CorpusNotFoundError, CorpusNotReadyError, InvalidCorpusIdError, ReservedCorpusError,
)
from constitutional_evidence_rag.corpora.models import Corpus, CorpusStatus
from constitutional_evidence_rag.ingestion.metadata import load_registry
from constitutional_evidence_rag.ingestion.pipeline import METADATA_FILENAME
from constitutional_evidence_rag.retrieval.store import BM25_KIND, DENSE_KIND, META_FILENAME

logger = get_logger(__name__)

CORPUS_FILENAME = "corpus.json"
LOCK_FILENAME = ".build.lock"
_CORPUS_ID = TypeAdapter(CorpusId)
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def validate_corpus_id(value: object) -> str:
    """A safe corpus id (also a path component): 1-64 of [a-z0-9-], not starting/ending with '-'."""
    try:
        return _CORPUS_ID.validate_python(value)
    except ValidationError:
        raise InvalidCorpusIdError(
            f"Invalid corpus id {str(value)[:70]!r}: use 1-64 lowercase letters, digits and hyphens, "
            "starting and ending with a letter or digit.") from None


def now() -> datetime:
    return datetime.now(timezone.utc)


class CorpusRegistry:
    def __init__(self, paths: PathsSettings, curated_id: str = CURATED_CORPUS_ID):
        self.processed_root = paths.data_processed_dir
        self.indexes_root = paths.indexes_dir
        self.uploads_root = paths.uploads_dir
        self.curated_id = curated_id

    # ------------------------------------------------------------------ locations

    def processed_dir(self, corpus_id: str) -> Path:
        return self.processed_root / validate_corpus_id(corpus_id)

    def indexes_dir(self, corpus_id: str) -> Path:
        return self.indexes_root / validate_corpus_id(corpus_id)

    def uploads_dir(self, corpus_id: str) -> Path:
        return self.uploads_root / validate_corpus_id(corpus_id)

    def is_reserved(self, corpus_id: str) -> bool:
        return corpus_id == self.curated_id

    # ------------------------------------------------------------------ read

    def exists(self, corpus_id: str) -> bool:
        return self.is_reserved(validate_corpus_id(corpus_id)) or (self.processed_dir(corpus_id) / CORPUS_FILENAME).exists()

    def get(self, corpus_id: str) -> Corpus:
        corpus_id = validate_corpus_id(corpus_id)
        if self.is_reserved(corpus_id):
            return self._inferred_curated()
        path = self.processed_dir(corpus_id) / CORPUS_FILENAME
        if not path.exists():
            raise CorpusNotFoundError(f"Corpus '{corpus_id}' does not exist.")
        return Corpus.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[Corpus]:
        corpora = [self._inferred_curated()]
        if self.processed_root.exists():
            for d in sorted(self.processed_root.iterdir()):
                if d.is_dir() and d.name != self.curated_id and (d / CORPUS_FILENAME).exists():
                    try:
                        corpora.append(self.get(d.name))
                    except Exception:  # noqa: BLE001 — one unreadable corpus.json must not break the listing
                        logger.warning("Skipping unreadable corpus record in %s", d.name)
        return corpora

    def ensure_queryable(self, corpus_id: str) -> Corpus:
        corpus = self.get(corpus_id)
        if corpus.status is CorpusStatus.READY:
            return corpus
        messages = {
            CorpusStatus.PROCESSING: f"Corpus '{corpus.corpus_id}' is still processing.",
            CorpusStatus.FAILED: f"Corpus '{corpus.corpus_id}' failed to build: {corpus.error or 'unknown error'}.",
            CorpusStatus.UPLOADED: f"Corpus '{corpus.corpus_id}' has not been built yet; build it before querying.",
        }
        raise CorpusNotReadyError(messages[corpus.status], corpus.status)

    def _inferred_curated(self) -> Corpus:
        processed = self.processed_root / self.curated_id
        chunks = processed / CHUNKS_FILENAME
        indexes = self.indexes_root / self.curated_id
        ready = chunks.exists() and (indexes / BM25_KIND / META_FILENAME).exists() and (indexes / DENSE_KIND / META_FILENAME).exists()
        stamp = datetime.fromtimestamp(chunks.stat().st_mtime, timezone.utc) if chunks.exists() else datetime(1970, 1, 1, tzinfo=timezone.utc)
        documents = 0
        if (processed / METADATA_FILENAME).exists():
            documents = sum(1 for m in load_registry(processed / METADATA_FILENAME) if m.replaced_by is None)
        chunk_count = sum(1 for line in chunks.open(encoding="utf-8") if line.strip()) if chunks.exists() else 0
        return Corpus(corpus_id=self.curated_id, name="Constitution of India and Supreme Court judgments (curated)",
                      status=CorpusStatus.READY if ready else CorpusStatus.UPLOADED, document_count=documents,
                      chunk_count=chunk_count, created_at=stamp, updated_at=stamp, reserved=True)

    # ------------------------------------------------------------------ write

    def create(self, corpus_id: str, name: str | None = None, owner_id: str | None = None) -> Corpus:
        corpus_id = validate_corpus_id(corpus_id)
        if self.is_reserved(corpus_id):
            raise ReservedCorpusError(f"Corpus '{corpus_id}' is reserved and cannot be created.")
        if self.exists(corpus_id):
            raise CorpusExistsError(f"Corpus '{corpus_id}' already exists.")
        stamp = now()
        corpus = Corpus(corpus_id=corpus_id, name=(name or corpus_id).strip()[:120] or corpus_id, status=CorpusStatus.UPLOADED,
                        created_at=stamp, updated_at=stamp, owner_id=owner_id)
        self.save(corpus)
        return corpus

    def save(self, corpus: Corpus) -> None:
        if corpus.reserved or self.is_reserved(corpus.corpus_id):
            raise ReservedCorpusError(f"Corpus '{corpus.corpus_id}' is reserved and cannot be modified.")
        directory = self.processed_dir(corpus.corpus_id)
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".corpus.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(corpus.model_dump_json(indent=2) + "\n")
            os.replace(tmp, directory / CORPUS_FILENAME)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def update(self, corpus_id: str, **changes) -> Corpus:
        corpus = self.get(corpus_id).model_copy(update={**changes, "updated_at": now()})
        self.save(corpus)
        return corpus

    def delete(self, corpus_id: str) -> None:
        corpus_id = validate_corpus_id(corpus_id)
        if self.is_reserved(corpus_id):
            raise ReservedCorpusError(f"Corpus '{corpus_id}' is reserved and cannot be deleted.")
        self.get(corpus_id)  # raises CorpusNotFoundError
        with self.lock(corpus_id):
            for root in (self.indexes_dir(corpus_id), self.uploads_dir(corpus_id)):
                shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(self.processed_dir(corpus_id), ignore_errors=True)

    # ------------------------------------------------------------------ locking and recovery

    @contextmanager
    def lock(self, corpus_id: str) -> Iterator[None]:
        """Exclusive build/modify lock for one corpus, in-process and across processes."""
        corpus_id = validate_corpus_id(corpus_id)
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(f"{self.processed_root.resolve()}::{corpus_id}", threading.Lock())
        if not thread_lock.acquire(blocking=False):
            raise CorpusBusyError(f"Corpus '{corpus_id}' is busy (a build or upload is in progress).")
        lock_file = self.processed_dir(corpus_id) / LOCK_FILENAME
        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise CorpusBusyError(f"Corpus '{corpus_id}' is busy (a build or upload is in progress).") from None
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps({"pid": os.getpid(), "since": now().isoformat()}))
            try:
                yield
            finally:
                lock_file.unlink(missing_ok=True)
        finally:
            thread_lock.release()

    def recover_stale(self) -> list[str]:
        """Startup: a corpus left in PROCESSING (crash) becomes FAILED; stale lock files are removed.
        Only call when no build can be running (service start)."""
        recovered = []
        for corpus in self.list():
            if corpus.reserved:
                continue
            (self.processed_dir(corpus.corpus_id) / LOCK_FILENAME).unlink(missing_ok=True)
            if corpus.status is CorpusStatus.PROCESSING:
                self.update(corpus.corpus_id, status=CorpusStatus.FAILED,
                            error="The build was interrupted before it finished (service restarted); build the corpus again")
                recovered.append(corpus.corpus_id)
        return recovered
