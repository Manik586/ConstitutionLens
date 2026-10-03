"""Citations (SRS FR-12; docs/DECISIONS.md D22).

A Citation is a projection of one EvidenceItem's metadata — document title and
date from the Phase 1 registry, pages / article / opinion author / division from
the Phase 2 chunk. Nothing is generated, so a citation cannot name a source,
page or article the evidence does not carry. Formatting:

    [1] The Constitution of India (as on 1st May, 2026), Article 21 (Protection of life and personal liberty), PDF p. 42
    [2] Maneka Gandhi v. Union of India (25 January 1978), opinion of BHAGWATI, J., PDF pp. 48–52
    [3] His Holiness Kesavananda Bharati Sripadagalvaru v. State of Kerala (24 April 1973), reporter's headnote, PDF p. 2

Page numbers are physical PDF pages (provenance page_start/page_end); printed
report pages are added only when the PDF defines page labels.
"""
from __future__ import annotations

from constitutional_evidence_rag.common.answer import Citation
from constitutional_evidence_rag.common.chunks import Division
from constitutional_evidence_rag.common.evidence import EvidenceItem
from constitutional_evidence_rag.common.models import SourceType


def build_citation(citation_id: int, item: EvidenceItem) -> Citation:
    c = item.chunk
    return Citation(
        citation_id=citation_id, evidence_id=item.evidence_id, chunk_id=c.chunk_id, document_id=c.document_id,
        document_version=c.document_version, source_type=c.source_type, title=item.document_title,
        source_date=item.source_date, article_number=c.article_number, article_title=c.article_title,
        opinion_author=c.opinion_author, division=c.division, page_start=c.page_start, page_end=c.page_end,
        page_label_start=c.page_label_start, page_label_end=c.page_label_end, source_url=c.source_url,
    )


def source_label(citation: Citation, show_author: bool = False) -> str:
    """What kind of text is being quoted — never 'the Court held' (V1 does not classify holdings)."""
    if citation.source_type is SourceType.CONSTITUTIONAL_TEXT:
        return f"Article {citation.article_number}" + (f" ({citation.article_title})" if citation.article_title else "") \
            if citation.article_number else "constitutional text"
    if citation.division is Division.FRONT_MATTER:
        return "reporter's headnote"
    if show_author and citation.opinion_author:
        return f"opinion of {citation.opinion_author}"
    return "judgment text"


def format_pages(citation: Citation) -> str:
    pdf = f"PDF p. {citation.page_start}" if citation.page_start == citation.page_end \
        else f"PDF pp. {citation.page_start}–{citation.page_end}"
    if citation.page_label_start:
        printed = citation.page_label_start if citation.page_label_start == citation.page_label_end \
            else f"{citation.page_label_start}–{citation.page_label_end}"
        return f"printed p. {printed} ({pdf})"
    return pdf


def format_citation(citation: Citation, show_author: bool = False) -> str:
    title = citation.title
    if citation.source_type is SourceType.JUDGMENT and citation.source_date:
        title += f" ({citation.source_date.day} {citation.source_date:%B %Y})"
    return f"[{citation.citation_id}] {title}, {source_label(citation, show_author)}, {format_pages(citation)}"


class CitationRegistry:
    """Numbers citations in order of first use; one number per evidence item."""

    def __init__(self) -> None:
        self._by_evidence: dict[str, Citation] = {}

    def cite(self, item: EvidenceItem) -> int:
        if item.evidence_id not in self._by_evidence:
            self._by_evidence[item.evidence_id] = build_citation(len(self._by_evidence) + 1, item)
        return self._by_evidence[item.evidence_id].citation_id

    @property
    def citations(self) -> list[Citation]:
        return sorted(self._by_evidence.values(), key=lambda c: c.citation_id)
