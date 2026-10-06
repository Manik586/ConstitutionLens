"""Apply claim-level evidence-support results to a Phase 5 answer (Phase 6; D26).

SUPPORTED     kept; citations that contribute nothing are dropped from the claim
INSUFFICIENT  removed from the answer, with a notice (Phase 5's removal policy)
CONTRADICTED  removed from the answer and flagged: listed in the validation report and
              in a notice — never presented as an established fact

Any removal makes the answer PARTIAL; if no claim survives it becomes
INSUFFICIENT_EVIDENCE (no claims, no citations; the validation report and the
evidence stay visible). Nothing is ever filled in from the model's own knowledge,
and the evidence itself is never modified.
"""
from __future__ import annotations

import re

from constitutional_evidence_rag.common.answer import Answer, AnswerClaim, AnswerStatus
from constitutional_evidence_rag.common.evidence import EvidenceSet
from constitutional_evidence_rag.common.validation import AnswerValidation, ClaimValidation, SupportStatus
from constitutional_evidence_rag.validation.claims import extract_claims
from constitutional_evidence_rag.validation.support import MARKER, SupportValidator

VALIDATED_STATUSES = (AnswerStatus.ANSWERED, AnswerStatus.PARTIAL)


def applies_to(answer: Answer) -> bool:
    """Phase 6 checks LLM-written answers. Extractive answers are verbatim quotations whose
    support is already enforced by the grounding validator."""
    return answer.citation_style == "evidence_id" and answer.status in VALIDATED_STATUSES


def apply_support_validation(answer: Answer, evidence: EvidenceSet, validator: SupportValidator) -> Answer:
    if not applies_to(answer):
        return answer
    by_id = evidence.by_id()
    records: list[ClaimValidation] = []
    statements: list[AnswerClaim] = []
    for statement in answer.claims:
        kept_sentences, kept_ids = [], []
        for claim in extract_claims(statement):
            result = validator.validate(claim.text, claim.citation_ids, by_id)
            keep = result.status is SupportStatus.SUPPORTED
            records.append(ClaimValidation(
                claim_id=claim.claim_id, statement_id=claim.statement_id, text=claim.text, citation_ids=claim.citation_ids,
                supporting_ids=result.supporting_ids, claim_type=claim.claim_type, status=result.status,
                score=result.score, coverage=result.coverage, semantic_score=result.semantic_score,
                reason=result.reason, kept=keep))
            if keep:
                kept_sentences.append(_with_markers(claim.sentence, result.supporting_ids))
                kept_ids += [i for i in result.supporting_ids if i not in kept_ids]
        if kept_sentences:
            statements.append(statement.model_copy(update={
                "statement": " ".join(kept_sentences), "citation_ids": [int(i[1:]) for i in kept_ids]}))

    validation = AnswerValidation(method=validator.method, claims=records)
    removed = [r for r in records if not r.kept]
    notices = list(answer.notices)
    for r in removed:
        label = "contradicts its cited evidence and was removed (flagged)" if r.status is SupportStatus.CONTRADICTED \
            else "was removed: its cited evidence does not confirm it"
        notices.append(f"Claim {r.claim_id} {label} — \"{r.text[:90]}\" ({r.reason})")
    if not statements:
        return answer.model_copy(update={
            "status": AnswerStatus.INSUFFICIENT_EVIDENCE, "claims": [], "citations": [], "validation": validation,
            "notices": notices + ["No generated claim was supported by its cited evidence."]})
    used = {i for s in statements for i in s.citation_ids}
    citations = [c for c in answer.citations if c.citation_id in used]
    status = AnswerStatus.PARTIAL if removed else answer.status
    # statement IDs (C1, C2, ...) are kept as generated, so validation records ("C2.1") still point at them
    return answer.model_copy(update={"claims": statements, "citations": citations, "status": status,
                                     "notices": notices, "validation": validation})


def _with_markers(sentence: str, ids: list[str]) -> str:
    """Keep only the markers of evidence that supports the sentence, and make sure each is shown."""
    def keep_supporting(match):
        kept = [i for i in (f"E{int(n)}" for n in re.findall(r"\d+", match.group(0))) if i in ids]
        return " ".join(f"[{i}]" for i in kept)
    text = " ".join(MARKER.sub(keep_supporting, sentence).split())
    missing = [i for i in ids if f"[{i}]" not in text]
    if missing:
        stripped = text.rstrip()
        end = stripped[-1] if stripped and stripped[-1] in ".;?!" else ""
        body = stripped[:-1].rstrip() if end else stripped
        text = f"{body} {' '.join(f'[{i}]' for i in missing)}{end}"
    return text.replace(" .", ".").replace(" ;", ";")
