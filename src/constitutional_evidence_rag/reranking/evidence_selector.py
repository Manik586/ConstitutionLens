"""Evidence selection above hybrid retrieval (Phase 4; docs/DECISIONS.md D20).

    candidates = Retriever.hybrid(query)            # Phase 3, unchanged
      + provision lookup  (the cited Article's own text, by article_number)
      + case lookup       (the named judgment's "HELD" headnote, by document_id)
    final_score = hybrid RRF score + locator boosts
        provision_match  chunk *is* a cited Article (constitutional text, article_number)
        case_match       chunk belongs to the named judgment (document_id)
        article_mention  judgment text mentions a cited Article ("Article 21", "Articles 14 and 21")
    order  = presentation group by query intent, then final_score
    select = top_k, at most max_per_document per document (not for the named case),
             at most max_constitutional constitutional items

Source type / authority never enters final_score (SRS §20.2, FR-SA-05). It only
decides the presentation group — the provision first for a PROVISION query,
judgments first for an INTERPRETATION query — which is labelling/display.
Every original retrieval score is kept on the EvidenceItem.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from constitutional_evidence_rag.common.chunks import Chunk, Division
from constitutional_evidence_rag.common.config import EvidenceSettings
from constitutional_evidence_rag.common.evidence import (
    EvidenceItem,
    EvidenceOrigin,
    EvidenceRole,
    EvidenceSet,
    QueryType,
)
from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType
from constitutional_evidence_rag.common.retrieval import RetrievedChunk
from constitutional_evidence_rag.query.understanding import QueryAnalysis
from constitutional_evidence_rag.retrieval.bm25 import tokenize

_ROLE = {SourceType.CONSTITUTIONAL_TEXT: EvidenceRole.CONSTITUTIONAL_TEXT, SourceType.JUDGMENT: EvidenceRole.JUDICIAL}


def role_of(chunk: Chunk) -> EvidenceRole:
    return _ROLE.get(chunk.source_type, EvidenceRole.OTHER)


def mentions_article(text: str, article: str) -> bool:
    """'Article 21', 'Art. 21', 'Articles 14, 19 and 21' — not a bare '21'."""
    pattern = rf"\bart(?:icle)?s?\.?\s+(?:[\w()]+(?:\s*,\s*|\s+and\s+|\s*&\s*))*{re.escape(article.lower())}(?![\w])"
    return re.search(pattern, " ".join(text.lower().split())) is not None


def is_held_headnote(chunk: Chunk) -> bool:
    return chunk.division is Division.FRONT_MATTER and (chunk.heading or "").strip().upper() == "HELD"


def select_evidence(
    analysis: QueryAnalysis,
    candidates: Sequence[RetrievedChunk],
    corpus_chunks: Sequence[Chunk],
    registry: Mapping[tuple[str, int], DocumentMetadata],
    settings: EvidenceSettings,
) -> EvidenceSet:
    notices: list[str] = []
    pool: dict[str, tuple[Chunk, RetrievedChunk | None, EvidenceOrigin]] = {
        r.chunk_id: (r.chunk, r, EvidenceOrigin.RETRIEVAL) for r in candidates
    }
    order = {c.chunk_id: i for i, c in enumerate(corpus_chunks)}

    # Provision lookup: the cited Articles' own text, even if retrieval missed it.
    for article in analysis.articles:
        own = [c for c in corpus_chunks if c.source_type is SourceType.CONSTITUTIONAL_TEXT and c.article_number == article]
        if not own:
            notices.append(f"No constitutional text for Article {article} was found in the corpus.")
        for c in own:
            pool.setdefault(c.chunk_id, (c, None, EvidenceOrigin.PROVISION_LOOKUP))

    # Case lookup: the named judgment's reporter headnote ("HELD"), else its opening chunk
    # (the reporter's catchword summary in SCR front matter).
    if analysis.names_unknown_case:
        notices.append("The case named in the question is not in the corpus.")
    case_summary_ids: set[str] = set()
    for document_id in analysis.case_document_ids:
        own = [c for c in corpus_chunks if c.document_id == document_id]
        for c in [c for c in own if is_held_headnote(c)][:2] or own[:1]:
            case_summary_ids.add(c.chunk_id)
            pool.setdefault(c.chunk_id, (c, None, EvidenceOrigin.CASE_LOOKUP))

    terms = analysis.content_terms
    scored = []
    for chunk_id, (chunk, retrieved, origin) in pool.items():
        boosts: dict[str, float] = {}
        reasons = [f"origin: {origin.value}"]
        is_provision = chunk.source_type is SourceType.CONSTITUTIONAL_TEXT and chunk.article_number in analysis.articles
        if is_provision:
            boosts["provision_match"] = settings.provision_match_boost
            reasons.append(f"is Article {chunk.article_number}")
        if chunk.document_id in analysis.case_document_ids:
            boosts["case_match"] = settings.case_match_boost
            reasons.append("belongs to the named case")
        mentioned = [a for a in analysis.articles if not is_provision and mentions_article(chunk.text, a)]
        if mentioned and role_of(chunk) is EvidenceRole.JUDICIAL:
            boosts["article_mention"] = settings.article_mention_boost
            reasons.append(f"mentions Article {', '.join(mentioned)}")
        coverage = _coverage(terms, chunk, set(analysis.articles) if is_provision else set())
        final = (retrieved.score if retrieved else 0.0) + sum(boosts.values())
        group = _group(analysis, chunk, is_provision, chunk_id in case_summary_ids)
        sort_key = (group, -final, retrieved.rank if retrieved else math.inf, order.get(chunk_id, math.inf))
        scored.append((sort_key, chunk, retrieved, origin, boosts, final, coverage, reasons))
    scored.sort(key=lambda s: s[0])

    # An INTERPRETATION question about an Article keeps one slot for that Article's own
    # text as supporting primary material, even when judgments fill the rest.
    provision_rows = [s for s in scored if "provision_match" in s[4]]
    reserved = 1 if (analysis.query_type is QueryType.INTERPRETATION and provision_rows and settings.max_constitutional) else 0

    chosen, per_doc, n_const = [], Counter(), 0
    for row in scored:
        if len(chosen) >= settings.top_k - reserved:
            break
        chunk = row[1]
        if role_of(chunk) is EvidenceRole.CONSTITUTIONAL_TEXT:
            if n_const >= settings.max_constitutional:
                continue
            n_const += 1
        elif chunk.document_id not in analysis.case_document_ids and per_doc[chunk.document_id] >= settings.max_per_document:
            continue
        per_doc[chunk.document_id] += 1
        chosen.append(row)
    if reserved and not any("provision_match" in r[4] for r in chosen):
        chosen.append(provision_rows[0])

    items: list[EvidenceItem] = []
    for _, chunk, retrieved, origin, boosts, final, coverage, reasons in chosen:
        role = role_of(chunk)
        meta = registry.get((chunk.document_id, chunk.document_version))
        items.append(EvidenceItem(
            evidence_id=f"E{len(items) + 1}", rank=len(items) + 1, role=role, origin=origin, chunk=chunk,
            document_title=meta.title if meta else (chunk.case_name or chunk.document_id),
            source_date=meta.source_date if meta else None,
            hybrid_rank=retrieved.rank if retrieved else None, hybrid_score=retrieved.score if retrieved else None,
            bm25_rank=retrieved.bm25_rank if retrieved else None, bm25_score=retrieved.bm25_score if retrieved else None,
            dense_rank=retrieved.dense_rank if retrieved else None, dense_score=retrieved.dense_score if retrieved else None,
            boosts=boosts, final_score=final, term_coverage=coverage, reasons=reasons,
        ))
    return EvidenceSet(query=analysis.query, query_type=analysis.query_type, items=items,
                       candidates_considered=len(pool), notices=notices)


def _group(analysis: QueryAnalysis, chunk: Chunk, is_provision: bool, is_case_summary: bool) -> int:
    """Presentation group by query intent (lower = earlier). Labelling, not scoring."""
    role = role_of(chunk)
    if analysis.query_type is QueryType.PROVISION:
        return 0 if is_provision else (1 if role is EvidenceRole.JUDICIAL else 2)
    if analysis.query_type is QueryType.INTERPRETATION:
        return 0 if role is EvidenceRole.JUDICIAL else (1 if is_provision else 2)
    if analysis.query_type is QueryType.CASE:
        if chunk.document_id in analysis.case_document_ids:
            # a bare "what did X decide/establish" question is best answered by the reporter's summary
            # first (the HELD headnote, else the judgment's opening), however it arrived
            return 0 if (is_case_summary and not analysis.content_terms) else 1
        return 2
    return 0


def _coverage(terms: Sequence[str], chunk: Chunk, matched_articles: set[str]) -> float:
    if not terms:
        return 1.0
    present = set(tokenize(chunk.text))
    hits = sum(1 for t in terms if t in present or t.upper() in matched_articles)
    return hits / len(terms)


def assess_confidence(analysis: QueryAnalysis, evidence: EvidenceSet, settings: EvidenceSettings) -> list[str]:
    """Reasons the evidence is insufficient to answer; empty list = sufficient."""
    problems = []
    if not evidence.items:
        problems.append("No evidence was retrieved for this question.")
    if analysis.names_unknown_case:
        problems.append("The case named in the question is not in the corpus.")
    if analysis.query_type is QueryType.CASE and analysis.case_document_ids and not any(
            i.chunk.document_id in analysis.case_document_ids for i in evidence.items):
        problems.append("No passage from the named judgment was retrieved.")
    if evidence.items and analysis.content_terms:
        best = max(i.term_coverage for i in evidence.items)
        if best < settings.min_term_coverage:
            problems.append(
                f"The best evidence contains only {best:.0%} of the question's key terms "
                f"(threshold {settings.min_term_coverage:.0%}): {', '.join(analysis.content_terms)}.")
    if settings.min_dense_score is not None:
        dense = [i.dense_score for i in evidence.items if i.dense_score is not None]
        if not dense or max(dense) < settings.min_dense_score:
            problems.append(f"Best dense similarity {max(dense) if dense else 'n/a'} is below "
                            f"the threshold {settings.min_dense_score}.")
    return problems
