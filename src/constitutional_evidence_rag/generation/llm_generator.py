"""Grounded LLM answer generation (Phase 5; docs/DECISIONS.md D23 - D25).

    EvidenceSet (Phase 4, ranked E1..En)
      -> build_context (context.py)  -> system + user prompt (prompts.py)
      -> LLMClient.complete          (llm.py; retried on malformed output, SRS FR-11)
      -> parse JSON payload          (status, direct_answer[], explanation[])
      -> per statement: citation IDs must be among the evidence actually sent; at least one
         citation; quoted text verbatim in a cited passage — otherwise the statement is removed
         and a notice says why
      -> Answer (citations built from evidence metadata only, citation_style "evidence_id")

The model contributes only statement text, evidence IDs and an explicit/inference basis.
Titles, pages, chunk IDs and URLs always come from the evidence (build_citation); anything
else the model returns (e.g. its own page numbers or case names) is ignored.
Failures never fall back to the model's own knowledge: provider errors, timeouts and
unusable replies give a GENERATION_FAILED answer, with the evidence still listed.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from constitutional_evidence_rag.citations.citation_builder import build_citation
from constitutional_evidence_rag.common.answer import (
    Answer,
    AnswerClaim,
    AnswerSection,
    AnswerStatus,
    ClaimBasis,
)
from constitutional_evidence_rag.common.config import GenerationSettings, LLMSettings
from constitutional_evidence_rag.common.evidence import EvidenceRole, EvidenceSet
from constitutional_evidence_rag.common.logging import get_logger
from constitutional_evidence_rag.generation.answer import insufficient_answer
from constitutional_evidence_rag.generation.context import build_context, citation_number
from constitutional_evidence_rag.generation.grounding import unverified_quotations
from constitutional_evidence_rag.generation.llm import LLMClient, LLMError, LLMRequest, LLMTimeoutError
from constitutional_evidence_rag.generation.prompts import build_user_prompt, system_prompt
from constitutional_evidence_rag.query.understanding import QueryAnalysis

logger = get_logger(__name__)

_MARKER = re.compile(r"\[\s*([Ee]\d+(?:\s*[,;]\s*[Ee]?\d+)*)\s*\]")
_ID = re.compile(r"^\[?\s*[Ee](\d+)\s*\]?$")
MAX_REASON_CHARS = 400


class MalformedOutputError(ValueError):
    """The model's reply is not the requested JSON object."""


class _Statement(BaseModel):
    model_config = ConfigDict(extra="ignore")  # any metadata the model adds is ignored, never trusted

    text: str
    citations: list[str] = []
    basis: Literal["explicit", "inference"] = "explicit"

    @field_validator("citations", mode="before")
    @classmethod
    def _ids(cls, value):
        """Accept "E1", "[E1]", "e1" or {"id": "E1", ...}; keep only the ID."""
        out = []
        for v in value or []:
            if isinstance(v, dict):
                v = v.get("id") or v.get("citation_id") or v.get("citation") or ""
            m = _ID.match(str(v).strip())
            if m:
                out.append(f"E{int(m.group(1))}")
            elif str(v).strip():
                out.append(str(v).strip())  # kept as-is so validation can reject it visibly
        return out

    @field_validator("text")
    @classmethod
    def _text(cls, value):
        if not value.strip():
            raise ValueError("empty statement")
        return " ".join(value.split())


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["answered", "insufficient_evidence"]
    direct_answer: list[_Statement] = []
    explanation: list[_Statement] = []
    insufficient_reason: str | None = None


def parse_payload(text: str) -> _Payload:
    """The JSON object in the model's reply (code fences and surrounding prose tolerated)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise MalformedOutputError("the reply contained no JSON object.")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise MalformedOutputError(f"the JSON could not be parsed ({exc.msg}).") from None
    try:
        payload = _Payload.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise MalformedOutputError(f"the JSON does not match the schema ({'.'.join(map(str, first['loc']))}: {first['msg']}).") from None
    if payload.status == "answered" and not (payload.direct_answer or payload.explanation):
        raise MalformedOutputError('status is "answered" but no statements were given.')
    return payload


class LLMAnswerGenerator:
    def __init__(self, generation: GenerationSettings, llm: LLMSettings, client: LLMClient):
        self.generation, self.llm, self.client = generation, llm, client
        self.name = f"llm/{client.provider}/{llm.model}/{generation.prompt_version}"

    # ------------------------------------------------------------------ public

    def generate(self, analysis: QueryAnalysis, evidence: EvidenceSet) -> Answer:
        context = build_context(evidence, self.generation)
        notices = list(evidence.notices) + context.notices
        if not context.included:
            return self._insufficient(analysis, evidence, notices + ["No evidence could be sent to the language model."])

        payload, feedback = None, None
        for attempt in range(self.llm.max_retries + 1):
            request = LLMRequest(
                system=system_prompt(self.generation.prompt_version),
                user=build_user_prompt(analysis.query, analysis.query_type, context.text, feedback),
                model=self.llm.model or "", temperature=self.llm.temperature,
                max_output_tokens=self.llm.max_output_tokens, timeout_seconds=self.llm.timeout_seconds,
                json_mode=self.llm.json_mode)
            try:
                response = self.client.complete(request)
            except LLMTimeoutError as exc:
                return self._failed(analysis, evidence, notices, f"The language model did not respond in time ({exc}).")
            except LLMError as exc:
                return self._failed(analysis, evidence, notices, f"The language model request failed: {exc}")
            try:
                payload = parse_payload(response.text)
                break
            except MalformedOutputError as exc:
                feedback = str(exc)
                logger.warning("LLM reply unusable (attempt %d/%d): %s", attempt + 1, self.llm.max_retries + 1, exc)
        if payload is None:
            return self._failed(analysis, evidence, notices,
                                f"The language model did not return a usable answer after {self.llm.max_retries + 1} attempt(s): {feedback}")

        if payload.status == "insufficient_evidence":
            reason = (payload.insufficient_reason or "the supplied evidence does not answer the question").strip()
            return self._insufficient(analysis, evidence, notices + [f"The language model reported insufficient evidence: {reason[:MAX_REASON_CHARS]}"])

        claims, removed = self._claims(payload, evidence, context.included)
        if not claims:
            return self._insufficient(analysis, evidence, notices + removed +
                                      ["None of the model's statements could be verified against the supplied evidence."])
        cited = list(dict.fromkeys(eid for c in claims for eid in c[1]))
        by_id = evidence.by_id()
        citations = [build_citation(citation_number(eid), by_id[eid]) for eid in cited]
        answer_claims = [AnswerClaim(claim_id=f"C{i}", section=section, statement=text,
                                     citation_ids=[citation_number(e) for e in ids], basis=ClaimBasis(basis))
                         for i, (text, ids, basis, section) in enumerate(claims, start=1)]
        partial = bool(removed) or any(n.startswith(("No constitutional text", "The case named")) for n in evidence.notices)
        return Answer(query=analysis.query, query_type=analysis.query_type,
                      status=AnswerStatus.PARTIAL if partial else AnswerStatus.ANSWERED,
                      claims=answer_claims, citations=citations, evidence=evidence.items,
                      notices=notices + removed, generator=self.name, citation_style="evidence_id")

    # ------------------------------------------------------------------ helpers

    def _claims(self, payload: _Payload, evidence: EvidenceSet, sent: list[str]):
        by_id = evidence.by_id()
        claims, removed = [], []
        statements = [(s, AnswerSection.ANSWER) for s in payload.direct_answer] + [(s, None) for s in payload.explanation]
        for n, (stmt, section) in enumerate(statements, start=1):
            in_text = [f"E{int(x)}" for group in _MARKER.findall(stmt.text) for x in re.findall(r"\d+", group)]
            ids = list(dict.fromkeys(in_text + stmt.citations))
            problem = None
            if not ids:
                problem = "it cites no evidence"
            elif bad := [i for i in ids if i not in sent]:
                where = "was not among the evidence supplied" if not all(i in by_id for i in bad) else "was not sent to the model"
                problem = f"it cites {', '.join(f'[{i}]' for i in bad)}, which {where}"
            if problem is None:
                text = _canonical_markers(stmt.text, ids)
                if quotes := unverified_quotations(text, [by_id[i].chunk.text for i in ids]):
                    problem = f"its quotation \"{quotes[0][:60]}\" is not verbatim in the cited evidence"
            if problem:
                removed.append(f"Statement {n} was removed because {problem}.")
                continue
            if section is None:
                roles = {by_id[i].role for i in ids}
                section = (AnswerSection.CONSTITUTIONAL_SOURCE if roles == {EvidenceRole.CONSTITUTIONAL_TEXT}
                           else AnswerSection.JUDICIAL_INTERPRETATION if EvidenceRole.JUDICIAL in roles
                           else AnswerSection.OTHER_EVIDENCE)
            claims.append((text, ids, stmt.basis, section))
        return claims, removed

    def _failed(self, analysis: QueryAnalysis, evidence: EvidenceSet, notices: list[str], reason: str) -> Answer:
        logger.warning("Generation failed: %s", reason)
        return Answer(query=analysis.query, query_type=analysis.query_type, status=AnswerStatus.GENERATION_FAILED,
                      evidence=evidence.items, notices=notices + [reason], generator=self.name, citation_style="evidence_id")

    def _insufficient(self, analysis: QueryAnalysis, evidence: EvidenceSet, notices: list[str]) -> Answer:
        answer = insufficient_answer(analysis, evidence, notices, self.name)
        return answer.model_copy(update={"citation_style": "evidence_id"})


def _canonical_markers(text: str, ids: list[str]) -> str:
    """Rewrite "[E1, E2]" / "[e1]" as "[E1] [E2]" and append markers for IDs listed but not shown."""
    text = _MARKER.sub(lambda m: " ".join(f"[E{int(x)}]" for x in re.findall(r"\d+", m.group(1))), text)
    missing = [i for i in ids if f"[{i}]" not in text]
    return (text + " " + " ".join(f"[{i}]" for i in missing)).strip() if missing else text
