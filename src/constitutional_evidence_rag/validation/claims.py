"""Deterministic claim extraction and claim -> citation mapping (Phase 6; D26).

Each Phase 5 answer statement (AnswerClaim) is split into sentence-level claims with
the project's legal-aware sentence splitter (no extra LLM call). A sentence's
citations are the [E#] markers it carries; a sentence without its own markers
inherits its statement's citations (the model attached those IDs to the whole
statement). Markers that are not well-formed [E#] IDs are never treated as citations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from constitutional_evidence_rag.common.answer import AnswerClaim, ClaimBasis
from constitutional_evidence_rag.common.validation import ClaimType
from constitutional_evidence_rag.generation.answer import split_sentences
from constitutional_evidence_rag.validation.support import MARKER, strip_markers

_MARKER_AFTER_PERIOD = re.compile(r"([.;?!])\s*((?:\[\s*E\d+(?:\s*[,;]\s*E?\d+)*\s*\]\s*)+)")
_INTERPRETIVE = re.compile(r"\b(interpret\w*|held|holds|construed|read|reads|observed|ruled|view)\b", re.I)
_QUOTE = re.compile(r"“[^”]{12,}”|\"[^\"]{12,}\"")


@dataclass(frozen=True)
class ExtractedClaim:
    claim_id: str
    statement_id: str
    sentence: str  # as written, with its own markers
    text: str  # without markers
    citation_ids: list[str]  # E-IDs, de-duplicated, in order
    claim_type: ClaimType


def marker_ids(text: str) -> list[str]:
    return list(dict.fromkeys(f"E{int(n)}" for m in MARKER.findall(text) for n in re.findall(r"\d+", m)))


def extract_claims(statement: AnswerClaim) -> list[ExtractedClaim]:
    inherited = [f"E{n}" for n in statement.citation_ids]
    # "… liberty. [E1] The Court …" -> "… liberty [E1]. The Court …" so markers stay with their sentence
    text = _MARKER_AFTER_PERIOD.sub(lambda m: f" {m.group(2).strip()}{m.group(1)} ", statement.statement)
    claims = []
    for sentence in split_sentences(text):
        clean = strip_markers(sentence)
        if not re.search(r"\w", clean):
            continue  # a sentence that is only markers adds no claim
        ids = marker_ids(sentence) or inherited
        claim_type = (ClaimType.QUOTATION if _QUOTE.search(clean)
                      else ClaimType.INTERPRETATIONAL if statement.basis is ClaimBasis.INFERENCE or _INTERPRETIVE.search(clean)
                      else ClaimType.PROPOSITIONAL)
        claims.append(ExtractedClaim(f"{statement.claim_id}.{len(claims) + 1}", statement.claim_id, sentence, clean, ids, claim_type))
    return claims
