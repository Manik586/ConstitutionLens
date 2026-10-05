"""Grounding validation (SRS FR-10, FR-12, NFR-02; docs/DECISIONS.md D22).

Run on every answer before it is returned, whatever generated it. It rejects:
* a citation whose evidence item was not selected for this query
  (i.e. citing a document that was not actually retrieved/selected);
* a citation whose metadata differs in any field from the evidence it points to
  (so a page, title, article or author cannot be invented or altered);
* a claim citing an unknown citation number, or missing its [n] marker;
* a quote that is not a verbatim (whitespace-normalized) span of a cited chunk;
* an INSUFFICIENT_EVIDENCE or GENERATION_FAILED answer that still carries claims or citations;
* (claims without a structured quote, i.e. LLM statements) text in quotation marks
  that is not verbatim in one of the claim's cited chunks.
"""
from __future__ import annotations

from constitutional_evidence_rag.citations.citation_builder import build_citation
from constitutional_evidence_rag.common.answer import Answer, AnswerStatus
from constitutional_evidence_rag.common.evidence import EvidenceSet
from constitutional_evidence_rag.generation.answer import normalize

import re

_QUOTED = re.compile(r"“([^”]+)”|\"([^\"]+)\"")
_ELLIPSIS = re.compile(r"\[?(?:…|\.\.\.)\]?")
_UNIFY = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
MIN_QUOTE_WORDS = 4  # shorter quoted fragments (terms, names) are not treated as quotations


def _canonical(text: str) -> str:
    return " ".join(text.translate(_UNIFY).lower().split())


def unverified_quotations(statement: str, source_texts: list[str]) -> list[str]:
    """Quoted spans in a statement that do not appear verbatim (case/whitespace/quote-style
    insensitive; "..." marks an omission) in any one of the given source texts."""
    sources = [_canonical(t) for t in source_texts]
    bad = []
    for m in _QUOTED.finditer(statement):
        quote = m.group(1) or m.group(2)
        if len(quote.split()) < MIN_QUOTE_WORDS:
            continue
        parts = [_canonical(p).strip(" ,;.") for p in _ELLIPSIS.split(quote)]
        parts = [p for p in parts if p]
        if not any(all(p in src for p in parts) for src in sources):
            bad.append(quote)
    return bad


class GroundingError(Exception):
    """The answer is not fully supported by the selected evidence."""


def validate_answer(answer: Answer, evidence: EvidenceSet) -> None:
    items = evidence.by_id()
    if [e.evidence_id for e in answer.evidence] != [e.evidence_id for e in evidence.items]:
        raise GroundingError("answer.evidence does not match the selected evidence")
    if answer.status in (AnswerStatus.INSUFFICIENT_EVIDENCE, AnswerStatus.GENERATION_FAILED) and (answer.claims or answer.citations):
        raise GroundingError(f"a {answer.status.value} answer must not contain claims or citations")

    citations = {}
    for c in answer.citations:
        if c.citation_id in citations:
            raise GroundingError(f"duplicate citation number [{c.citation_id}]")
        item = items.get(c.evidence_id)
        if item is None or item.chunk.chunk_id != c.chunk_id:
            raise GroundingError(f"citation [{c.citation_id}] points to {c.chunk_id!r}, which was not among the selected evidence")
        if build_citation(c.citation_id, item) != c:
            raise GroundingError(f"citation [{c.citation_id}] metadata differs from its evidence ({c.evidence_id})")
        citations[c.citation_id] = c

    for claim in answer.claims:
        for cid in claim.citation_ids:
            if cid not in citations:
                raise GroundingError(f"claim {claim.claim_id} cites unknown citation [{cid}]")
            marker = answer.marker(citations[cid])
            if marker not in claim.statement:
                raise GroundingError(f"claim {claim.claim_id} does not show its citation marker {marker}")
        if claim.quote is not None:
            parts = [p.strip() for p in claim.quote.split("[…]") if p.strip()]
            sources = [normalize(items[citations[cid].evidence_id].chunk.text) for cid in claim.citation_ids]
            if not parts or not any(all(p in text for p in parts) for text in sources):
                raise GroundingError(f"claim {claim.claim_id} quotes text not found verbatim in its cited evidence")
            if f"“{claim.quote}”" not in claim.statement:
                raise GroundingError(f"claim {claim.claim_id} statement does not present its quote verbatim")
        else:
            sources = [items[citations[cid].evidence_id].chunk.text for cid in claim.citation_ids]
            if bad := unverified_quotations(claim.statement, sources):
                raise GroundingError(f"claim {claim.claim_id} quotes text not found verbatim in its cited evidence: {bad[0][:60]!r}")
