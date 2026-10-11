"""Corpus service layer (Phase 7; D27 - D29). Framework-independent: the CLI scripts and the
FastAPI app both call it. Reuses the corpus-generic Phase 1-6 code unchanged:

    add_documents   validate + store uploads (uploads.py)                 -> status UPLOADED
    build_corpus    ingest (Phase 1) -> chunk (Phase 2) -> indexes (Phase 3, atomic swap)
                    under the corpus lock: PROCESSING -> READY | FAILED
    start_build     the same on one background worker thread (replaceable by a job queue later)
    query_corpus    status check BEFORE any index is touched -> the existing Phase 4/5/6
                    AnswerPipeline, bound to this corpus (isolation asserted before generation)

One embedding model instance is loaded lazily and shared; per-corpus retrievers are cached
by corpus id and invalidated whenever that corpus changes.
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from constitutional_evidence_rag.chunking.legal_chunker import CHUNKS_FILENAME, chunk_corpus, load_chunks
from constitutional_evidence_rag.common.answer import Answer
from constitutional_evidence_rag.common.config import Settings
from constitutional_evidence_rag.common.logging import get_logger
from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.corpora.errors import (
    CorpusBuildError, CorpusBusyError, CorpusError, ReservedCorpusError,
)
from constitutional_evidence_rag.corpora.indexing import build_indexes_atomically
from constitutional_evidence_rag.corpora.models import Corpus, CorpusStatus
from constitutional_evidence_rag.corpora.registry import CorpusRegistry
from constitutional_evidence_rag.corpora.uploads import FileResult, UploadStore
from constitutional_evidence_rag.generation.llm import LLMConfigurationError, make_llm_client
from constitutional_evidence_rag.generation.pipeline import AnswerPipeline, CorpusIsolationError, answering_settings, make_generator
from constitutional_evidence_rag.ingestion.metadata import DocumentSpec, load_registry
from constitutional_evidence_rag.ingestion.pipeline import DOCUMENTS_FILENAME, METADATA_FILENAME, ingest_documents
from constitutional_evidence_rag.query.understanding import CaseIndex
from constitutional_evidence_rag.retrieval.dense import make_embedder
from constitutional_evidence_rag.retrieval.hybrid import Retriever
from constitutional_evidence_rag.retrieval.store import QueryError

logger = get_logger(__name__)

USER_CORPUS_NOTICE = ("Answers are generated from your uploaded documents only; they are not legal advice and not "
                      "an authoritative statement of law.")


def safe_reason(exc: BaseException) -> str:
    """A short, user-safe reason: CorpusError messages are written to be shown; anything else
    is reduced to its type so server paths or internals never reach a user."""
    if isinstance(exc, CorpusError):
        return exc.message.rstrip(".")
    return f"internal error during the build ({type(exc).__name__}); see the server log"


class CorpusService:
    def __init__(self, settings: Settings, *, embedder_factory=make_embedder, llm_client_factory=make_llm_client):
        self.settings = settings
        self.registry = CorpusRegistry(settings.paths)
        self._embedder_factory, self._llm_client_factory = embedder_factory, llm_client_factory
        self._embedder = None
        self._embedder_lock = threading.Lock()
        self._cache: dict[str, tuple[str, Retriever, dict]] = {}
        self._cache_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._builds: dict[str, Future] = {}

    # ------------------------------------------------------------------ lifecycle

    def startup(self) -> list[str]:
        """Create data directories, recover builds interrupted by a crash, log the (non-secret) config."""
        p = self.settings.paths
        for directory in (p.data_processed_dir, p.indexes_dir, p.uploads_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if p.models_dir is not None:
            os.environ.setdefault("HF_HOME", str(p.models_dir))
        recovered = self.registry.recover_stale()
        logger.info("Corpus service: default corpus %s; embedding model %s; LLM provider %s; upload limits %s MB/file, "
                    "%s documents, %s MB/corpus; recovered %s", self.settings.corpus.default_corpus_id,
                    self.settings.embedding.model_name, self.settings.llm.provider, self.settings.uploads.max_file_size_mb,
                    self.settings.uploads.max_documents_per_corpus, self.settings.uploads.max_total_size_mb, recovered or "none")
        return recovered

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True)

    def embedder(self):
        with self._embedder_lock:
            if self._embedder is None:
                self._embedder = self._embedder_factory(self.settings.embedding)
            return self._embedder

    # ------------------------------------------------------------------ corpora

    def create_corpus(self, corpus_id: str, name: str | None = None) -> Corpus:
        return self.registry.create(corpus_id, name)

    def get_corpus(self, corpus_id: str) -> Corpus:
        return self.registry.get(corpus_id)

    def list_corpora(self) -> list[Corpus]:
        return self.registry.list()

    def delete_corpus(self, corpus_id: str) -> None:
        self.registry.delete(corpus_id)
        self._invalidate(corpus_id)

    # ------------------------------------------------------------------ documents

    def _writable(self, corpus_id: str, action: str, *, resuming: bool = False) -> Corpus:
        corpus = self.registry.get(corpus_id)
        if corpus.reserved:
            raise ReservedCorpusError(f"Corpus '{corpus.corpus_id}' is reserved; {action}.")
        if corpus.status is CorpusStatus.PROCESSING and not resuming:
            raise CorpusBusyError(f"Corpus '{corpus.corpus_id}' is still processing; try again when the build finishes.")
        return corpus

    def add_documents(self, corpus_id: str, files: list[tuple[str | None, bytes]]) -> tuple[Corpus, list[FileResult]]:
        corpus = self._writable(corpus_id, "documents cannot be uploaded to it")
        with self.registry.lock(corpus.corpus_id):
            store = UploadStore(self.registry.uploads_dir(corpus.corpus_id))
            results = store.add(files, self.settings.uploads)
            if any(r.status == "accepted" for r in results):
                corpus = self.registry.update(corpus.corpus_id, status=CorpusStatus.UPLOADED, error=None)
        return corpus, results

    def add_documents_from_dir(self, corpus_id: str, directory: Path) -> tuple[Corpus, list[FileResult]]:
        files = [(f.name, f.read_bytes()) for f in sorted(directory.iterdir()) if f.is_file() and f.suffix.lower() == ".pdf"]
        if not files:
            raise CorpusBuildError(f"No .pdf files found in {directory.name!r}.")
        return self.add_documents(corpus_id, files)

    # ------------------------------------------------------------------ build stages

    def _run_stage(self, corpus_id: str, work, final_status: CorpusStatus, *, resuming: bool = False):
        corpus = self._writable(corpus_id, "it cannot be rebuilt through the user-corpus service", resuming=resuming)
        with self.registry.lock(corpus.corpus_id):
            self.registry.update(corpus.corpus_id, status=CorpusStatus.PROCESSING, error=None)
            try:
                result = work(corpus.corpus_id)
            except BaseException as exc:
                logger.exception("Build of corpus %s failed", corpus.corpus_id)
                self.registry.update(corpus.corpus_id, status=CorpusStatus.FAILED, error=safe_reason(exc))
                self._invalidate(corpus.corpus_id)
                if isinstance(exc, CorpusError):
                    raise
                raise CorpusBuildError(f"Corpus '{corpus.corpus_id}' failed to build: {safe_reason(exc)}.") from exc
            self.registry.update(corpus.corpus_id, status=final_status, error=None, **self._counts(corpus.corpus_id))
            self._invalidate(corpus.corpus_id)
            return result

    def _ingest(self, corpus_id: str):
        store = UploadStore(self.registry.uploads_dir(corpus_id))
        entries = store.entries()
        if not entries:
            raise CorpusBuildError(f"Corpus '{corpus_id}' has no documents; upload PDFs first.")
        documents = [(store.directory / name, DocumentSpec(source_type=SourceType.USER_DOCUMENT, title=e["title"],
                                                           source_url=f"upload://{corpus_id}/{e['sha256']}"))
                     for name, e in sorted(entries.items())]
        report = ingest_documents(documents, corpus_id=corpus_id, processed_root=self.settings.paths.data_processed_dir,
                                  base_dir=store.directory)
        if not report.ingested and not report.unchanged:
            raise CorpusBuildError("none of the documents could be ingested")
        return report

    def _chunk(self, corpus_id: str):
        return chunk_corpus(self.settings.paths.data_processed_dir, corpus_id, self.settings.chunking)

    def _index(self, corpus_id: str):
        chunks = load_chunks(self.registry.processed_dir(corpus_id) / CHUNKS_FILENAME)
        if not chunks:
            raise CorpusBuildError("the documents produced no text chunks")
        if any(c.corpus_id != corpus_id for c in chunks):
            raise CorpusIsolationError(f"chunks of another corpus found in '{corpus_id}'")
        return build_indexes_atomically(chunks, self.registry.indexes_dir(corpus_id), self.embedder())

    def ingest_uploads(self, corpus_id: str):
        return self._run_stage(corpus_id, self._ingest, CorpusStatus.UPLOADED)

    def chunk(self, corpus_id: str):
        return self._run_stage(corpus_id, self._chunk, CorpusStatus.UPLOADED)

    def index(self, corpus_id: str):
        return self._run_stage(corpus_id, self._index, CorpusStatus.READY)

    def build_corpus(self, corpus_id: str, *, _resuming: bool = False) -> Corpus:
        """ingest -> chunk -> index, all under one lock; READY on success, FAILED (with reason) otherwise."""
        def work(cid):
            self._ingest(cid)
            self._chunk(cid)
            self._index(cid)
        self._run_stage(corpus_id, work, CorpusStatus.READY, resuming=_resuming)
        return self.registry.get(corpus_id)

    def start_build(self, corpus_id: str) -> Corpus:
        """Start build_corpus on the single background worker and return immediately (status PROCESSING)."""
        corpus = self._writable(corpus_id, "it cannot be rebuilt through the user-corpus service")
        with self.registry.lock(corpus.corpus_id):  # fails fast if a build is running elsewhere
            corpus = self.registry.update(corpus.corpus_id, status=CorpusStatus.PROCESSING, error=None)
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="corpus-build")

        def run():
            try:
                self.build_corpus(corpus.corpus_id, _resuming=True)  # status stays PROCESSING throughout
            except CorpusError:
                pass  # status already FAILED with a user-safe reason
            except BaseException:  # noqa: BLE001
                logger.exception("Background build of %s crashed", corpus.corpus_id)

        self._builds[corpus.corpus_id] = self._executor.submit(run)
        return corpus

    def wait_for_build(self, corpus_id: str, timeout: float | None = None) -> Corpus:
        future = self._builds.get(corpus_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.registry.get(corpus_id)

    # ------------------------------------------------------------------ query

    def generation_available(self) -> bool:
        """Whether `generate=True` queries can run: the configured LLM client can be built.
        Builds the client only (no network call); exposes nothing about the provider."""
        try:
            self._llm_client_factory(self.settings.llm)
        except LLMConfigurationError:
            return False
        return True

    def query_corpus(self, corpus_id: str, question: str, *, mode: str = "hybrid", top_k: int | None = None,
                     generate: bool = False) -> Answer:
        corpus = self.registry.ensure_queryable(corpus_id)  # before any index is touched
        if mode != "hybrid":
            raise QueryError("Answers are built from hybrid retrieval; use mode 'hybrid'.")
        if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
            raise QueryError("top_k must be a positive integer.")
        settings = self._query_settings(top_k, generate)
        generator = make_generator(settings, self._llm_client_factory(settings.llm) if generate else None)
        retriever, documents = self._resources(corpus)
        pipeline = AnswerPipeline(retriever, documents, settings, generator, embedder=self.embedder(), corpus_id=corpus.corpus_id,
                                  reference_cases=None if corpus.reserved else self._library_cases(),
                                  title_texts=None if corpus.reserved else self._title_texts(corpus))
        answer = pipeline.answer(question)
        if not corpus.reserved:
            answer = answer.model_copy(update={"notices": [*answer.notices, USER_CORPUS_NOTICE]})
        return answer

    def _query_settings(self, top_k: int | None, generate: bool) -> Settings:
        s = self.settings
        generation = s.generation.model_copy(update={"backend": "llm" if generate else "extractive"})
        evidence = s.evidence
        if top_k is not None:
            evidence = evidence.model_copy(update={"top_k": top_k})
            generation = generation.model_copy(update={"context_max_items": top_k})
        return s.model_copy(update={"generation": generation, "evidence": evidence})

    def _library_cases(self) -> CaseIndex | None:
        """The curated library's judgments (titles only), read from its registry. Used so a user
        corpus can name the cases it holds and flag named cases it lacks; no curated text is used."""
        metadata = self.registry.processed_dir(self.registry.curated_id) / METADATA_FILENAME
        if not metadata.exists():
            return None
        stamp = metadata.stat().st_mtime
        cached = getattr(self, "_library_cache", None)
        if cached is None or cached[0] != stamp:
            judgments = [(m.document_id, m.title) for m in load_registry(metadata)
                         if m.replaced_by is None and m.source_type is SourceType.JUDGMENT]
            self._library_cache = (stamp, CaseIndex(judgments))
        return self._library_cache[1]

    def _title_texts(self, corpus: Corpus) -> dict[str, str]:
        """document_id -> start of the first page's text (title area), from documents.jsonl.
        Cached per corpus version; only the first 1000 characters are kept."""
        version = corpus.updated_at.isoformat()
        cache = self.__dict__.setdefault("_title_cache", {})
        if cache.get(corpus.corpus_id, (None,))[0] == version:
            return cache[corpus.corpus_id][1]
        texts: dict[str, str] = {}
        path = self.registry.processed_dir(corpus.corpus_id) / DOCUMENTS_FILENAME
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    pages = row.get("pages") or []
                    if pages:
                        texts[row["document_id"]] = str(pages[0].get("text", ""))[:1000]
        cache[corpus.corpus_id] = (version, texts)
        return texts

    def _resources(self, corpus: Corpus) -> tuple[Retriever, dict]:
        version = corpus.updated_at.isoformat()
        with self._cache_lock:
            cached = self._cache.get(corpus.corpus_id)
            if cached and cached[0] == version:
                return cached[1], cached[2]
        p = self.settings.paths
        retriever = Retriever.load(processed_root=p.data_processed_dir, indexes_root=p.indexes_dir,
                                   corpus_id=corpus.corpus_id, settings=answering_settings(self.settings),
                                   embedder=self.embedder())
        if any(c.corpus_id != corpus.corpus_id for c in retriever.chunks):
            raise CorpusIsolationError(f"chunks of another corpus found in '{corpus.corpus_id}'")
        metadata = self.registry.processed_dir(corpus.corpus_id) / METADATA_FILENAME
        documents = {(m.document_id, m.document_version): m for m in load_registry(metadata)} if metadata.exists() else {}
        with self._cache_lock:
            self._cache[corpus.corpus_id] = (version, retriever, documents)
        return retriever, documents

    def _invalidate(self, corpus_id: str) -> None:
        with self._cache_lock:
            self._cache.pop(corpus_id, None)

    def _counts(self, corpus_id: str) -> dict:
        processed = self.registry.processed_dir(corpus_id)
        documents = sum(1 for m in load_registry(processed / METADATA_FILENAME) if m.replaced_by is None) \
            if (processed / METADATA_FILENAME).exists() else 0
        chunks_path = processed / CHUNKS_FILENAME
        chunks = sum(1 for line in chunks_path.open(encoding="utf-8") if line.strip()) if chunks_path.exists() else 0
        return {"document_count": documents, "chunk_count": chunks}
