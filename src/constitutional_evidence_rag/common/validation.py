"""Claim-level evidence-support results (Phase 6; SRS FR-13 Basic Evidence Check; D26).

These record whether the retrieved evidence a claim cites *textually* supports it.
They say nothing about legal correctness, precedent validity or whether a case is
still good law.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SupportStatus(str, Enum):
    SUPPORTED = "supported"  # the cited evidence states what the claim says
    CONTRADICTED = "contradicted"  # an explicit conflict between claim and cited evidence was detected
    INSUFFICIENT = "insufficient"  # support could not be confirmed (the conservative default)


class ClaimType(str, Enum):
    QUOTATION = "quotation"
    INTERPRETATIONAL = "interpretational"
    PROPOSITIONAL = "propositional"


class ClaimValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str  # "C2.1" = first claim extracted from answer statement C2
    statement_id: str
    text: str  # the claim without citation markers
    citation_ids: list[str]  # evidence IDs the claim cites, e.g. ["E1", "E3"]
    supporting_ids: list[str] = Field(default_factory=list)  # cited items that actually contribute support
    claim_type: ClaimType
    status: SupportStatus
    score: float = Field(ge=0, le=1)
    coverage: float | None = Field(default=None, ge=0, le=1)
    semantic_score: float | None = None
    reason: str
    kept: bool  # whether the claim remains in the final answer


class AnswerValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    claims: list[ClaimValidation]

    def count(self, status: SupportStatus) -> int:
        return sum(c.status is status for c in self.claims)
