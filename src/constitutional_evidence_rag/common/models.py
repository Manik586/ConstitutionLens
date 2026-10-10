"""Core document/metadata models shared across pipeline stages.

Mirrors the `documents` table schema in docs/SRS.md Section 24
field-for-field, per docs/DECISIONS.md D4: Phase 1 persists these as
JSONL, not SQLite, but the schema is written to match so the later
migration is a storage-backend swap, not a redesign. `corpus_id` is the
one column added by the SRS v4.2 amendment (docs/SRS_v4.2_AMENDMENT.md,
D15): every document belongs to exactly one corpus. V1 has a single
curated corpus, CURATED_CORPUS_ID; V2 user collections are further
corpus IDs handled by the same code.

SourceType is intentionally limited to the three V1 values (SRS Section
21.1, FR-04). V2's finer 4-level authority taxonomy (SRS Section 20) is
out of scope here and belongs in the verification module when that
phase begins — see docs/ARCHITECTURE.md Section 4.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

CURATED_CORPUS_ID = "constitutional-core"
"""The V1 curated corpus: Constitution of India + selected SC judgments."""

CorpusId = Annotated[str, Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")]
"""Lower-case slug (1-64 chars). Also used as a directory name under
data/processed/, so the pattern deliberately excludes '/', '.', and '..'."""


class SourceType(str, Enum):
    """Kinds of source document (SRS Section 21.1, FR-04; D27).

    CONSTITUTIONAL_TEXT / AMENDMENT / JUDGMENT describe the curated corpus.
    USER_DOCUMENT is one neutral type for PDFs a user uploads into their own
    corpus (Phase 7): no structure is assumed and no authority is implied — it is
    not part of the SRS Section 20 authority taxonomy, which remains a separate,
    later concern.
    """

    CONSTITUTIONAL_TEXT = "constitutional_text"
    AMENDMENT = "amendment"
    JUDGMENT = "judgment"
    USER_DOCUMENT = "user_document"  # Phase 7: user-uploaded PDF (D27)


class DocumentMetadata(BaseModel):
    """A registered source document (SRS Section 24 `documents` table).

    `document_version` and `replaced_by` implement re-ingestion
    versioning (SRS NFR-07, FR-01): re-ingesting a source creates a new
    version rather than overwriting the prior one.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    corpus_id: CorpusId
    source_type: SourceType
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    document_version: int = Field(default=1, ge=1)
    source_date: date | None = None
    ingestion_date: datetime
    replaced_by: str | None = None
