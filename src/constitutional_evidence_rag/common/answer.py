"""Answer, claims and citations (SRS FR-11 structured output, FR-12 citation
fields, §21.5 response schema, FR-14/FR-17 non-advice notice; D21, D22).

Every citation is built from evidence metadata and every quoted claim is a
verbatim span of a cited chunk; generation/grounding.py enforces both before
an answer is returned."""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from constitutional_evidence_rag.common.chunks import Division
from constitutional_evidence_rag.common.evidence import EvidenceItem, QueryType
from constitutional_evidence_rag.common.models import SourceType

DISCLAIMER = (
    "This is a legal research tool, not legal advice. Passages are quoted from retrieved sources; "
    "evidentiary support is not a determination of legal correctness or of whether a precedent is still good law."
)


class AnswerStatus(str, Enum):
    ANSWERED = "answered"
    PARTIAL = "partial"  # answered, but a requested source was not found (see notices)
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AnswerSection(str, Enum):
    ANSWER = "answer"
    CONSTITUTIONAL_SOURCE = "constitutional_source"
    JUDICIAL_INTERPRETATION = "judicial_interpretation"
    OTHER_EVIDENCE = "other_evidence"


class Citation(BaseModel):
    """FR-12 fields, all copied from evidence metadata — never generated."""

    model_config = ConfigDict(extra="forbid")

    citation_id: int = Field(ge=1)
    evidence_id: str
    chunk_id: str
    document_id: str
    document_version: int
    source_type: SourceType
    title: str
    source_date: date | None = None
    article_number: str | None = None
    article_title: str | None = None
    opinion_author: str | None = None
    division: Division | None = None
    page_start: int
    page_end: int
    page_label_start: str | None = None
    page_label_end: str | None = None
    source_url: str


class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str  # "C1", ...
    section: AnswerSection
    statement: str  # rendered text: attribution + quote + citation markers
    quote: str | None = None  # verbatim (whitespace-normalized) span of a cited chunk; " […] " marks omissions
    citation_ids: list[int] = Field(min_length=1)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    query_type: QueryType
    status: AnswerStatus
    claims: list[AnswerClaim] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    generator: str
    disclaimer: str = DISCLAIMER
