"""Core document/metadata models shared across pipeline stages.

Mirrors the `documents` table schema in docs/SRS.md Section 24
field-for-field, per docs/DECISIONS.md D4: Phase 1 persists these as
JSONL, not SQLite, but the schema is written to match so the later
migration is a storage-backend swap, not a redesign.

SourceType is intentionally limited to the three V1 values (SRS Section
21.1, FR-04). V2's finer 4-level authority taxonomy (SRS Section 20) is
out of scope here and belongs in the verification module when that
phase begins — see docs/ARCHITECTURE.md Section 4.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    """V1 source types (SRS Section 21.1, FR-04).

    Do not add V2's authority levels (constitutional text / SC holding /
    other judgment content / secondary material) here — that taxonomy
    is a separate, later concern. See SRS Section 20.
    """

    CONSTITUTIONAL_TEXT = "constitutional_text"
    AMENDMENT = "amendment"
    JUDGMENT = "judgment"


class DocumentMetadata(BaseModel):
    """A registered source document (SRS Section 24 `documents` table).

    `document_version` and `replaced_by` implement re-ingestion
    versioning (SRS NFR-07, FR-01): re-ingesting a source creates a new
    version rather than overwriting the prior one.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    source_type: SourceType
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    document_version: int = Field(default=1, ge=1)
    source_date: date | None = None
    ingestion_date: datetime
    replaced_by: str | None = None
