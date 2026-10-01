"""Chunk model — the Phase 2 output consumed by Phase 3 retrieval
(SRS FR-03, Section 24 `chunks`, NFR-03; docs/DECISIONS.md D16).

Every chunk is self-contained provenance-wise:

    chunk text -> pages page_start..page_end -> document_id@document_version
               -> corpus_id -> source_url

FR-03 requires each field to be "populated or explicitly null", so the
SRS-listed nullable fields (article_number, case_name, paragraph_number)
are always present. Structure fields are filled only when the chunker
actually detected that structure; they are never guessed.

`section_type` (SRS Section 24, FR-JS-01) is deliberately absent: it is a
V2 classification. Judgment headings are recorded verbatim in `heading`,
without mapping them to that taxonomy.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from constitutional_evidence_rag.common.models import CorpusId, SourceType


class ChunkingMethod(str, Enum):
    STRUCTURE = "structure"  # the chunk is one whole detected unit (an article, a headed section)
    STRUCTURE_SPLIT = "structure_split"  # part of a detected unit that exceeded max_tokens
    FALLBACK = "fallback"  # no structure detected; size-based packing at line boundaries


class Division(str, Enum):
    """Coarse position within a document, only when a boundary was detected."""

    FRONT_MATTER = "front_matter"  # before the first article / before the first opinion
    BODY = "body"  # Constitution: from the first Part/Preamble/article onward
    OPINION = "opinion"  # judgment: after "Judgment ... delivered by" or an opinion author line


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # identity & provenance
    chunk_id: str = Field(min_length=1)
    corpus_id: CorpusId
    document_id: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    source_type: SourceType
    source_url: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    page_label_start: str | None = None  # printed label, only if the PDF defines page labels
    page_label_end: str | None = None

    # content
    text: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    chunking_method: ChunkingMethod

    # detected structure (null when not detected)
    division: Division | None = None
    part: str | None = None  # Constitution, e.g. "PART III — FUNDAMENTAL RIGHTS"
    chapter: str | None = None  # Constitution, e.g. "CHAPTER I.—THE EXECUTIVE"
    article_number: str | None = None  # SRS FR-03 nullable field, e.g. "21A"
    article_title: str | None = None
    clause_labels: list[str] = Field(default_factory=list)  # top-level clauses starting in this chunk, e.g. ["(1)", "(2)"]
    heading: str | None = None  # nearest detected heading, verbatim
    opinion_author: str | None = None  # judgment, e.g. "VERMA, CJI."
    case_name: str | None = None  # SRS FR-03 nullable field; the judgment's title
    paragraph_number: str | None = None  # SRS FR-03 nullable field; not yet detected (D16)

    @model_validator(mode="after")
    def _check(self) -> Chunk:
        if self.page_start > self.page_end:
            raise ValueError(f"page_start {self.page_start} > page_end {self.page_end}")
        if not self.text.strip():
            raise ValueError("chunk text is blank")
        if self.article_number is not None and self.source_type is not SourceType.CONSTITUTIONAL_TEXT:
            raise ValueError("article_number is only set on constitutional_text chunks")
        return self
