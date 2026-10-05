"""Question -> grounded answer (Phase 4; docs/DECISIONS.md D20 - D22).

    validate query -> analyze_query (query/understanding.py)
    -> Retriever.hybrid with a wider candidate pool (Phase 3, unchanged)
    -> select_evidence (reranking/evidence_selector.py) -> assess_confidence
    -> generator.generate (extractive, or the Phase 5 LLM generator — make_generator),
       or an explicit insufficient-evidence answer (the generator is then never called)
    -> validate_answer (generation/grounding.py) -> Answer
"""
from __future__ import annotations

from pathlib import Path

from constitutional_evidence_rag.common.answer import Answer
from constitutional_evidence_rag.common.config import Settings
from constitutional_evidence_rag.common.evidence import EvidenceSet
from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType
from constitutional_evidence_rag.generation.answer import AnswerGenerator, ExtractiveAnswerGenerator, insufficient_answer
from constitutional_evidence_rag.generation.grounding import validate_answer
from constitutional_evidence_rag.generation.llm import LLMClient, make_llm_client
from constitutional_evidence_rag.generation.llm_generator import LLMAnswerGenerator
from constitutional_evidence_rag.ingestion.metadata import load_registry
from constitutional_evidence_rag.ingestion.pipeline import METADATA_FILENAME, corpus_dir
from constitutional_evidence_rag.query.understanding import CaseIndex, QueryAnalysis, analyze_query
from constitutional_evidence_rag.reranking.evidence_selector import assess_confidence, select_evidence
from constitutional_evidence_rag.retrieval.dense import Embedder
from constitutional_evidence_rag.retrieval.hybrid import Retriever
from constitutional_evidence_rag.retrieval.store import IndexNotFoundError, validate_query


def answering_settings(settings: Settings) -> Settings:
    """Same settings, with BM25/dense/hybrid top-n widened to evidence.candidate_pool."""
    pool = settings.evidence.candidate_pool
    retrieval = settings.retrieval.model_copy(update={"top_n_bm25": pool, "top_n_dense": pool, "top_n_hybrid": pool})
    return settings.model_copy(update={"retrieval": retrieval})


def make_generator(settings: Settings, llm_client: LLMClient | None = None) -> AnswerGenerator:
    """generation.backend selects the generator. The LLM client is built from settings.llm unless
    one is passed in (tests, or a caller that already built it); a misconfiguration raises
    LLMConfigurationError here, before any retrieval work is done."""
    if settings.generation.backend == "llm":
        client = llm_client or make_llm_client(settings.llm)
        return LLMAnswerGenerator(settings.generation, settings.llm, client)
    return ExtractiveAnswerGenerator(settings.generation)


class AnswerPipeline:
    def __init__(self, retriever: Retriever, registry: dict[tuple[str, int], DocumentMetadata], settings: Settings,
                 generator: AnswerGenerator | None = None):
        self.retriever, self.registry, self.settings = retriever, registry, settings
        self.generator = generator or make_generator(settings)
        current = [m for m in registry.values() if m.replaced_by is None and m.source_type is SourceType.JUDGMENT]
        self.cases = CaseIndex((m.document_id, m.title) for m in current)

    @classmethod
    def load(cls, *, processed_root: Path, indexes_root: Path, corpus_id: str, settings: Settings,
             embedder: Embedder | None = None, generator: AnswerGenerator | None = None) -> AnswerPipeline:
        settings = answering_settings(settings)
        metadata_path = corpus_dir(processed_root, corpus_id) / METADATA_FILENAME
        if not metadata_path.exists():
            raise IndexNotFoundError(f"No document registry at {metadata_path} — run scripts/ingest_corpus.py first")
        registry = {(m.document_id, m.document_version): m for m in load_registry(metadata_path)}
        retriever = Retriever.load(processed_root=processed_root, indexes_root=indexes_root, corpus_id=corpus_id,
                                   settings=settings, embedder=embedder)
        return cls(retriever, registry, settings, generator)

    def analyze(self, query: str) -> QueryAnalysis:
        return analyze_query(validate_query(query), self.cases)

    def select(self, analysis: QueryAnalysis) -> EvidenceSet:
        candidates = self.retriever.hybrid(analysis.query, self.settings.evidence.candidate_pool)
        return select_evidence(analysis, candidates, self.retriever.chunks, self.registry, self.settings.evidence)

    def answer(self, query: str) -> Answer:
        analysis = self.analyze(query)
        evidence = self.select(analysis)
        problems = assess_confidence(analysis, evidence, self.settings.evidence)
        if problems:
            answer = insufficient_answer(analysis, evidence, evidence.notices + problems, self.generator.name)
        else:
            answer = self.generator.generate(analysis, evidence)
        validate_answer(answer, evidence)
        return answer
