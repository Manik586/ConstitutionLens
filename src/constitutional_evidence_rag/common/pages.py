"""Page-level parsed content models (SRS NFR-03: Traceability).

These are the Phase 1 ingestion output that Phase 2 chunking consumes,
so they live in `common/` rather than inside `ingestion/`. They are kept
out of `models.py` only because they embed `Provenance`, and
`provenance.py` already imports from `models.py` — placing them there
would create a circular import. See docs/DECISIONS.md D11.

The invariant these models enforce, at construction time, is:

    page text  ->  page (1-based page_number)  ->  source document (id + version)

A `ParsedDocument` whose pages do not all point back to that same
document/version, or whose page numbers are not exactly 1..N in order,
fails validation. Traceability is therefore a property of the data
shape, not something each consumer has to remember to check.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.common.provenance import Provenance


class ParsedPage(BaseModel):
    """The extracted text of one physical PDF page, with its provenance.

    Pages with no extractable text are kept (with `is_empty=True`)
    rather than dropped, so page numbering never shifts and a gap in the
    text is visible downstream instead of silently disappearing.
    """

    model_config = ConfigDict(extra="forbid")

    provenance: Provenance
    text: str
    is_empty: bool
    page_label: str | None = None
    extraction_error: str | None = None

    @model_validator(mode="after")
    def _check_page(self) -> ParsedPage:
        if self.provenance.page_number is None:
            raise ValueError("a parsed page's provenance must carry a page_number")
        if self.is_empty != (not self.text.strip()):
            raise ValueError("is_empty must be True exactly when text has no non-whitespace content")
        return self


class ParsedDocument(BaseModel):
    """One ingested version of a source document, as an ordered list of pages.

    `source_file` is the PDF's path relative to the raw data directory
    (POSIX-style), so records stay valid across machines and containers.
    `file_sha256` identifies the exact bytes that were parsed; it is how
    re-ingestion decides whether a new version is needed (D10).
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    source_type: SourceType
    source_file: str = Field(min_length=1)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    pages: list[ParsedPage]

    @model_validator(mode="after")
    def _check_traceability(self) -> ParsedDocument:
        if self.page_count != len(self.pages):
            raise ValueError(f"page_count={self.page_count} but {len(self.pages)} pages present")

        for expected_number, page in enumerate(self.pages, start=1):
            prov = page.provenance
            if prov.page_number != expected_number:
                raise ValueError(
                    f"pages must be numbered 1..N in order; expected page {expected_number}, "
                    f"got {prov.page_number}"
                )
            if (prov.document_id, prov.document_version, prov.source_type) != (
                self.document_id,
                self.document_version,
                self.source_type,
            ):
                raise ValueError(
                    f"page {expected_number} provenance points to "
                    f"{prov.document_id}@v{prov.document_version} ({prov.source_type.value}), "
                    f"not {self.document_id}@v{self.document_version} ({self.source_type.value})"
                )
        return self

    @property
    def empty_page_numbers(self) -> list[int]:
        return [p.provenance.page_number for p in self.pages if p.is_empty]  # type: ignore[misc]
