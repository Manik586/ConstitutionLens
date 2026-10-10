"""User-safe errors for the corpus service (Phase 7). Messages never contain server paths,
API keys or tracebacks; `code` is a stable machine-readable identifier for the API."""
from __future__ import annotations


class CorpusError(Exception):
    code = "corpus_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidCorpusIdError(CorpusError):
    code = "invalid_corpus_id"


class ReservedCorpusError(CorpusError):
    code = "reserved_corpus"


class CorpusExistsError(CorpusError):
    code = "corpus_exists"


class CorpusNotFoundError(CorpusError):
    code = "corpus_not_found"


class CorpusBusyError(CorpusError):
    code = "corpus_busy"


class CorpusNotReadyError(CorpusError):
    code = "corpus_not_ready"

    def __init__(self, message: str, status):
        super().__init__(message)
        self.status = status


class CorpusBuildError(CorpusError):
    code = "corpus_build_failed"
