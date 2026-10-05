"""Evidence context builder (Phase 5; docs/DECISIONS.md D24).

Turns Phase 4's final evidence (EvidenceSet, already ranked E1, E2, ...) into the
text block the LLM sees. Deterministic: the same evidence always yields the same
string. Every field comes from the evidence item — the Phase 2 chunk, the Phase 1
registry title/date, and the Phase 3/4 scores — via the same helpers that build
citations, so the model sees exactly the provenance its citations will carry.
A field the evidence does not have is omitted, never filled in. (There is no
court field in the chunk model; judge names follow generation.show_opinion_author.)

Long chunk text is truncated per item, and items beyond the total budget are left
out; both are marked in the context and returned as notices.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from constitutional_evidence_rag.citations.citation_builder import build_citation, format_pages, source_label
from constitutional_evidence_rag.common.config import GenerationSettings
from constitutional_evidence_rag.common.evidence import EvidenceItem, EvidenceSet
from constitutional_evidence_rag.common.models import SourceType

TRUNCATION_MARK = " [… text truncated for length]"
_DOC_TYPE = {SourceType.CONSTITUTIONAL_TEXT: "constitutional text", SourceType.JUDGMENT: "judgment",
             SourceType.AMENDMENT: "constitutional amendment material"}


@dataclass(frozen=True)
class EvidenceContext:
    text: str
    included: list[str]  # evidence ids sent to the model, in order
    truncated: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


def citation_number(evidence_id: str) -> int:
    return int(evidence_id[1:])


def _neutralize(text: str) -> str:
    """Evidence text is data: stop it from imitating context markers or our own citation IDs."""
    text = re.sub(r"\[\s*(E\d+)\s*\]", r"(\1)", text)
    return text.replace("<evidence", "&lt;evidence").replace("</evidence", "&lt;/evidence")


def _section(item: EvidenceItem) -> str | None:
    c = item.chunk
    if c.source_type is SourceType.CONSTITUTIONAL_TEXT and c.article_number:
        return f"Article {c.article_number}" + (f" — {c.article_title}" if c.article_title else "")
    parts = [p for p in (c.part, c.chapter, c.heading) if p]
    return " / ".join(parts) if parts else None


def _scores(item: EvidenceItem) -> str:
    if item.hybrid_rank is None:
        origin = item.origin.value.replace("_", " ")
        return f"added by {origin} (not ranked by retrieval); selection score {item.final_score:.4f}"
    parts = [f"hybrid rank {item.hybrid_rank} (RRF {item.hybrid_score:.4f})"]
    if item.bm25_rank:
        parts.append(f"BM25 rank {item.bm25_rank}")
    if item.dense_rank:
        parts.append(f"dense rank {item.dense_rank}")
    return ", ".join(parts) + f"; selection score {item.final_score:.4f}"


def format_item(item: EvidenceItem, text: str, truncated: bool, show_author: bool) -> str:
    c = item.chunk
    citation = build_citation(citation_number(item.evidence_id), item)
    lines = [f"[{item.evidence_id}]", f"Source: {item.document_title}"]
    lines.append(f"Document type: {_DOC_TYPE.get(c.source_type, c.source_type.value.replace('_', ' '))}")
    if item.source_date and c.source_type is SourceType.JUDGMENT:
        lines.append(f"Date: {item.source_date.day} {item.source_date:%B %Y}")
    if section := _section(item):
        lines.append(f"Section: {section}")
    if c.source_type is not SourceType.CONSTITUTIONAL_TEXT:
        lines.append(f"Passage type: {source_label(citation, show_author)}")
    lines += [f"Pages: {format_pages(citation)}", f"Document ID: {c.document_id} (version {c.document_version})",
              f"Chunk ID: {c.chunk_id}", f"Source URL: {c.source_url}", f"Retrieval: {_scores(item)}"]
    lines.append("Text" + (" (truncated)" if truncated else "") + ":")
    lines.append(f"<evidence id=\"{item.evidence_id}\">\n{text}\n</evidence>")
    return "\n".join(lines)


def build_context(evidence: EvidenceSet, settings: GenerationSettings) -> EvidenceContext:
    blocks, included, truncated, omitted = [], [], [], []
    used = 0
    for item in evidence.items:
        if len(included) >= settings.context_max_items:
            omitted.append(item.evidence_id)
            continue
        text = " ".join(_neutralize(item.chunk.text).split())
        was_cut = len(text) > settings.context_max_chars_per_item
        if was_cut:
            text = text[: settings.context_max_chars_per_item].rsplit(" ", 1)[0] + TRUNCATION_MARK
        block = format_item(item, text, was_cut, settings.show_opinion_author)
        if included and used + len(block) > settings.context_max_total_chars:
            omitted.append(item.evidence_id)
            continue
        blocks.append(block)
        included.append(item.evidence_id)
        used += len(block)
        if was_cut:
            truncated.append(item.evidence_id)
    notices = []
    if truncated:
        notices.append(f"Evidence {', '.join(truncated)} was truncated to {settings.context_max_chars_per_item} characters "
                       "for the language model.")
    if omitted:
        notices.append(f"Evidence {', '.join(omitted)} was not sent to the language model (context limit); "
                       "it remains listed for inspection.")
    return EvidenceContext(text="\n\n".join(blocks), included=included, truncated=truncated, omitted=omitted, notices=notices)
