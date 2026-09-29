"""Provenance representation (SRS Section 18, NFR-03: Traceability).

Every downstream artifact must be traceable back to
document_id -> source_type -> version -> source_url, plus a page number
where extractable. This module defines that minimal, reusable trail for
Phase 1 (document- and page-level only); chunk-level provenance
(paragraph_number, case_name, article_number) belongs to Phase 2's
chunking module (SRS Section 21.1, FR-03), not here.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType


class Provenance(BaseModel):
    """The traceability trail attached to a piece of extracted content.

    `page_number` is the 1-based physical page index within the source
    PDF (page 1 = first page of the file), not the printed page label —
    printed labels such as law-report pagination are carried separately
    by the page record that owns this provenance (see common/pages.py).
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    source_type: SourceType
    document_version: int = Field(ge=1)
    source_url: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)


def provenance_from_document(
    document: DocumentMetadata, page_number: int | None = None
) -> Provenance:
    """Derive a Provenance record from a document's metadata.

    Keeps the document -> provenance field mapping in one place so
    parsing code (ingestion/pdf_parser.py) doesn't have to hand-copy
    these fields itself.
    """
    return Provenance(
        document_id=document.document_id,
        source_type=document.source_type,
        document_version=document.document_version,
        source_url=document.source_url,
        page_number=page_number,
    )
