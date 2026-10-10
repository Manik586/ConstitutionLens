"""Corpus model (Phase 7; D28). Stored as data/processed/<corpus_id>/corpus.json."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from constitutional_evidence_rag.common.models import CorpusId


class CorpusStatus(str, Enum):
    UPLOADED = "uploaded"  # has (or awaits) documents; not built yet, or changed since the last build
    PROCESSING = "processing"  # ingest / chunk / index running
    READY = "ready"  # indexes built from the current documents; queryable
    FAILED = "failed"  # last build failed; see `error`


class Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_id: CorpusId
    name: str
    status: CorpusStatus
    document_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    error: str | None = None  # short, user-safe
    owner_id: str | None = None  # reserved for future authentication; unused
    reserved: bool = False  # the curated corpus: read-only through the user-corpus service/API
