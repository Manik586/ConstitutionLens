"""Answer generation (SRS FR-10 - FR-14; docs/DECISIONS.md D21).

`AnswerGenerator` is the interface; `ExtractiveAnswerGenerator` is the only
backend until an LLM provider is chosen (open decision, SRS Phase 5). It is
deterministic and grounded by construction:

* every claim is a **verbatim quote** (whitespace-normalized) from a selected
  evidence item, chosen by overlap with the question's content terms;
  omissions inside a quote are marked " […] ";
* attribution is neutral and metadata-driven — "Article 21 (…)", "reporter's
  headnote", "opinion of X" — never "the Court held", because V1 does not
  classify holdings vs arguments (FR-JS-02/03 are V2);
* constitutional text and judicial passages go to separate sections;
* nothing is paraphrased, summarized or inferred, so nothing can be fabricated.

Every answer — from this or any future backend — is checked by
generation/grounding.py before it is returned (D22).
"""
from __future__ import annotations

import re
from typing import Protocol

from constitutional_evidence_rag.citations.citation_builder import CitationRegistry, format_citation, source_label
from constitutional_evidence_rag.common.answer import Answer, AnswerClaim, AnswerSection, AnswerStatus, ClaimBasis
from constitutional_evidence_rag.common.config import GenerationSettings
from constitutional_evidence_rag.common.evidence import EvidenceItem, EvidenceRole, EvidenceSet, QueryType
from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.query.understanding import QueryAnalysis
from constitutional_evidence_rag.retrieval.bm25 import tokenize

ELLIPSIS = " […] "
_SECTION = {EvidenceRole.CONSTITUTIONAL_TEXT: AnswerSection.CONSTITUTIONAL_SOURCE,
            EvidenceRole.JUDICIAL: AnswerSection.JUDICIAL_INTERPRETATION,
            EvidenceRole.OTHER: AnswerSection.OTHER_EVIDENCE}
_ABBREVIATIONS = frozenset(
    "v vs no nos art arts s ss cl cls sub j jj cj cji c ors anr etc viz ie eg p pp para paras sec secs mr ms mrs dr "
    "hon'ble ltd co govt supp scr scc air vol ch sch".split()
)
_BOUNDARY = re.compile(r"[.;?!][\"'”’)]*\s+(?=[\"“‘(]?[A-Z0-9])")


class AnswerGenerator(Protocol):
    name: str

    def generate(self, analysis: QueryAnalysis, evidence: EvidenceSet) -> Answer: ...


def normalize(text: str) -> str:
    return " ".join(text.split())


def split_sentences(text: str) -> list[str]:
    """Sentence spans of whitespace-normalized text; does not split after legal
    abbreviations (v., Art., J., S.C.R.) or single-letter initials (A.N.)."""
    text = normalize(text)
    sentences, start = [], 0
    for m in _BOUNDARY.finditer(text):
        word = re.findall(r"[\w'.]+$", text[start:m.start() + 1])
        last = word[0].rstrip(".").split(".")[-1].lower() if word else ""
        if text[m.start()] == "." and (last in _ABBREVIATIONS or len(last) == 1):
            continue
        sentences.append(text[start:m.end()].strip())
        start = m.end()
    if text[start:].strip():
        sentences.append(text[start:].strip())
    return sentences


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + ELLIPSIS.rstrip()


def _relevance(sentence: str, terms: set[str], articles: list[str]) -> int:
    score = len(terms & set(tokenize(sentence)))
    low = sentence.lower()
    return score + sum(2 for a in articles if re.search(rf"\bart(?:icle)?s?\.?\s+(?:[\w()]+,?\s+(?:and\s+)?)*{re.escape(a.lower())}\b", low))


def select_quote(item: EvidenceItem, analysis: QueryAnalysis, settings: GenerationSettings) -> str | None:
    """A verbatim quote from the item, or None if no sentence is relevant to the question."""
    text = normalize(item.chunk.text)
    if item.role is EvidenceRole.CONSTITUTIONAL_TEXT and item.chunk.article_number in analysis.articles:
        return _truncate(text, settings.max_quote_chars)  # the provision itself, from its start
    # A chunk can begin mid-sentence (chunk boundary); such a fragment is verbatim but misleading out of context.
    sentences = [s for s in split_sentences(text) if not s[0].islower()] or split_sentences(text)
    terms = set(analysis.content_terms)
    if not terms and analysis.query_type is QueryType.CASE:
        if item.chunk.document_id in analysis.case_document_ids:
            picked = sentences[: settings.sentences_per_item]  # "What did X establish?" -> the named judgment's own text
            return _truncate(" ".join(picked), settings.max_quote_chars)
        terms = set(analysis.case_terms)  # elsewhere: passages that mention the named case
    if not terms and not analysis.articles:
        picked = sentences[: settings.sentences_per_item]
    else:
        ranked = sorted(range(len(sentences)), key=lambda i: (-_relevance(sentences[i], terms, analysis.articles), i))
        picked_idx = sorted(i for i in ranked[: settings.sentences_per_item] if _relevance(sentences[i], terms, analysis.articles) > 0)
        if not picked_idx:
            return None
        picked, previous = [], None
        for i in picked_idx:
            if previous is not None and i != previous + 1:
                picked.append("…")  # marker for a gap, replaced below
            picked.append(sentences[i])
            previous = i
    quote = " ".join(picked).replace(" … ", ELLIPSIS)
    return _truncate(quote, settings.max_quote_chars)


class ExtractiveAnswerGenerator:
    name = "extractive-v1"

    def __init__(self, settings: GenerationSettings):
        self.settings = settings

    def generate(self, analysis: QueryAnalysis, evidence: EvidenceSet) -> Answer:
        registry = CitationRegistry()
        claims: list[AnswerClaim] = []
        for item in evidence.items:
            quote = select_quote(item, analysis, self.settings)
            if quote is None:
                continue
            cid = registry.cite(item)
            citation = registry.citations[cid - 1]
            lead = not claims  # the best item opens the answer
            claims.append(AnswerClaim(
                claim_id=f"C{len(claims) + 1}",
                section=AnswerSection.ANSWER if lead else _SECTION[item.role],
                statement=_statement(item, citation, quote, cid, analysis, lead, self.settings.show_opinion_author),
                quote=quote, citation_ids=[cid],
            ))
        notices = list(evidence.notices)
        if not claims:
            return insufficient_answer(analysis, evidence, notices + ["No retrieved passage addresses the question's key terms."], self.name)
        partial = any(n.startswith(("No constitutional text", "The case named")) for n in notices)
        return Answer(query=analysis.query, query_type=analysis.query_type,
                      status=AnswerStatus.PARTIAL if partial else AnswerStatus.ANSWERED, claims=claims,
                      citations=registry.citations, evidence=evidence.items, notices=notices, generator=self.name)


def _statement(item: EvidenceItem, citation, quote: str, cid: int, analysis: QueryAnalysis, lead: bool,
               show_author: bool) -> str:
    c = item.chunk
    if c.source_type is SourceType.CONSTITUTIONAL_TEXT and c.article_number:
        verb = "reads" if c.article_number in analysis.articles else "provides"
        return f"Article {c.article_number} of the Constitution{f' ({c.article_title})' if c.article_title else ''} {verb}: “{quote}” [{cid}]"
    year = f" ({item.source_date.year})" if item.source_date else ""
    if lead and analysis.query_type is QueryType.CASE and item.chunk.document_id in analysis.case_document_ids:
        return f"According to the {source_label(citation, show_author)} of {item.document_title}{year}: “{quote}” [{cid}]"
    return f"{item.document_title}{year}, {source_label(citation, show_author)}: “{quote}” [{cid}]"


def insufficient_answer(analysis: QueryAnalysis, evidence: EvidenceSet, reasons: list[str], generator: str) -> Answer:
    """No claims, no citations; the evidence and reasons stay visible for inspection."""
    return Answer(query=analysis.query, query_type=analysis.query_type, status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                  evidence=evidence.items, notices=list(dict.fromkeys(reasons)), generator=generator)


_HEADINGS = [(AnswerSection.ANSWER, "Answer"), (AnswerSection.CONSTITUTIONAL_SOURCE, "Constitutional source"),
             (AnswerSection.JUDICIAL_INTERPRETATION, "Judicial interpretation (quoted passages)"),
             (AnswerSection.OTHER_EVIDENCE, "Other evidence")]


def render_text(answer: Answer, show_evidence: bool = False, show_author: bool = False) -> str:
    lines = [f"Query type: {answer.query_type.value} | status: {answer.status.value} | generator: {answer.generator}", ""]
    if answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE:
        lines += ["Answer:", "  The available evidence in the corpus is insufficient to answer this question reliably.", ""]
    elif answer.status is AnswerStatus.GENERATION_FAILED:
        lines += ["Answer:", "  No answer was generated (see Notes). The retrieved evidence is listed below for inspection.", ""]
    quoted = answer.citation_style == "number"  # extractive answers are verbatim quotations
    for section, heading in _HEADINGS:
        claims = [c for c in answer.claims if c.section is section]
        if claims:
            lines.append(f"{heading if quoted else heading.replace(' (quoted passages)', '')}:")
            lines += [f"  {'(inference) ' if c.basis is ClaimBasis.INFERENCE else ''}{c.statement}" for c in claims]
            lines.append("")
    if answer.citations:
        lines.append("Citations:")
        lines += [f"  {format_citation(c, show_author, answer.marker(c))}" for c in answer.citations]
        lines.append("")
    if answer.notices:
        lines.append("Notes:")
        lines += [f"  - {n}" for n in answer.notices]
        lines.append("")
    if show_evidence or answer.status in (AnswerStatus.INSUFFICIENT_EVIDENCE, AnswerStatus.GENERATION_FAILED):
        lines.append("Evidence considered (final score | hybrid rank/score | bm25 rank | dense rank | boosts):")
        for e in answer.evidence:
            hybrid = f"#{e.hybrid_rank} {e.hybrid_score:.4f}" if e.hybrid_rank else "lookup"
            lines.append(f"  {e.evidence_id} {e.final_score:.4f} | {hybrid} | bm25 #{e.bm25_rank or '-'} | dense #{e.dense_rank or '-'}"
                         f" | {e.boosts or {}} | {e.document_title[:50]} PDF pp. {e.chunk.page_start}-{e.chunk.page_end} | {e.chunk.chunk_id}")
        lines.append("")
    lines.append(answer.disclaimer)
    return "\n".join(lines)
