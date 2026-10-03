"""Evidence selected for answering — the Phase 4 intermediate representation
(D20). Auditable: each item keeps the complete Phase 2 Chunk (all provenance),
the document title/date from the Phase 1 registry, every original retrieval
score, each boost applied, and the reasons it was selected."""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from constitutional_evidence_rag.common.chunks import Chunk


class QueryType(str, Enum):
    PROVISION = "provision"  # "What does Article 14 provide?"
    INTERPRETATION = "interpretation"  # "How has the Supreme Court interpreted Article 21?"
    CASE = "case"  # "What did Maneka Gandhi v. Union of India decide?"
    GENERAL = "general"


class EvidenceRole(str, Enum):
    """Presentation role from source_type (labelling only, SRS §20.2)."""

    CONSTITUTIONAL_TEXT = "constitutional_text"
    JUDICIAL = "judicial"
    OTHER = "other"


class EvidenceOrigin(str, Enum):
    RETRIEVAL = "retrieval"  # came from hybrid retrieval
    PROVISION_LOOKUP = "provision_lookup"  # the named Article's own text, resolved by article_number
    CASE_LOOKUP = "case_lookup"  # the named judgment's headnote, resolved by document_id


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str  # "E1", "E2", ... in final order
    rank: int = Field(ge=1)
    role: EvidenceRole
    origin: EvidenceOrigin
    chunk: Chunk
    document_title: str
    source_date: date | None = None

    # original retrieval evidence (None when the item came from a lookup, not retrieval)
    hybrid_rank: int | None = None
    hybrid_score: float | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None

    boosts: dict[str, float] = Field(default_factory=dict)
    final_score: float
    term_coverage: float = Field(ge=0, le=1)  # share of the query's content terms this item contains
    reasons: list[str] = Field(default_factory=list)


class EvidenceSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    query_type: QueryType
    items: list[EvidenceItem]
    candidates_considered: int
    notices: list[str] = Field(default_factory=list)

    def by_id(self) -> dict[str, EvidenceItem]:
        return {e.evidence_id: e for e in self.items}
