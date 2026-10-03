"""Query understanding (Phase 4; D20). Deterministic rules, no model.

    CASE            names a judgment in the corpus, or has the "X v. Y" form
    INTERPRETATION  asks how courts read something ("interpreted", "the Court held/said",
                    "judgments on", "doctrine", "case law", ...)
    PROVISION       cites an Article ("What does Article 14 provide?") without interpretation cues
    GENERAL         anything else

Checked in that order, so "What did Maneka Gandhi v. Union of India decide?" is a
CASE query and "How has the Supreme Court interpreted Article 21?" is an
INTERPRETATION query. Article references are extracted generically (14, 19(1)(g),
21A, 32 ...). Case names are resolved only against judgment titles that are
actually in the corpus, using tokens unique to one case.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from constitutional_evidence_rag.common.evidence import QueryType
from constitutional_evidence_rag.retrieval.bm25 import tokenize

_ARTICLE_REF = re.compile(r"\b(?:articles?|arts?\.?)\s+((?:\d{1,3}[A-Za-z]{0,3}(?:\s?\(\s?\w{1,4}\s?\))*(?:\s*,\s*|\s+and\s+|\s*&\s*|\s+or\s+)?)+)", re.I)
_ARTICLE_NUM = re.compile(r"(\d{1,3}[A-Za-z]{0,3})((?:\s?\(\s?\w{1,4}\s?\))*)")
_CASE_FORM = re.compile(r"\b[A-Z][\w.]*(?:\s+[\w.&]+){0,6}\s+(?:v\.|vs\.?|versus)\s+[A-Z]", re.U)
_INTERPRETATION = re.compile(
    r"\binterpret\w*|\b(?:court|bench|judges?)\s+(?:has\s+|have\s+)?(?:say|said|says|held|hold|holds|ruled?|rulings?|decided|"
    r"observed|read|construed|explained)\b|\bjudgments?\b|\bjudicial\b|\bcase[- ]law\b|\bprecedents?\b|\bdoctrines?\b|"
    r"\bhow\s+(?:has|have|did)\s+(?:the\s+)?(?:supreme\s+)?courts?\b",
    re.I,
)

# Words that carry no evidential content in a question; removed before measuring coverage.
QUERY_STOPWORDS = frozenset(
    "what does did do how has have is are was were provide provides provided say says said mean means meaning explain "
    "describe tell about under regarding concerning according article articles art arts constitution constitutional "
    "india indian supreme court courts interpret interpreted interpretation held hold holds decide decided decision "
    "establish established significance significant case cases judgment judgments law laws".split()
)
# Name parts too common to identify one case.
_GENERIC_NAME_TOKENS = frozenset(
    "his holiness and ors anr others another etc the of ltd limited state states union india dead lrs by through thr "
    "secretary ministry law justice government govt citizens welfare forum industries mills foundation company society "
    "association council board corporation co pvt private public commissioner director collector".split()
)


class CaseIndex:
    """Judgment titles in the corpus -> distinctive name tokens -> document ids."""

    def __init__(self, documents: Iterable[tuple[str, str]]):
        """documents: (document_id, title) for each judgment."""
        self.titles: dict[str, str] = {}
        tokens_by_doc: dict[str, set[str]] = {}
        for document_id, title in documents:
            self.titles[document_id] = title
            tokens_by_doc[document_id] = {
                t for t in re.findall(r"[a-z]+", title.lower()) if len(t) >= 4 and t not in _GENERIC_NAME_TOKENS
            }
        counts = Counter(t for tokens in tokens_by_doc.values() for t in tokens)
        self.distinctive: dict[str, set[str]] = {
            d: {t for t in tokens if counts[t] == 1} for d, tokens in tokens_by_doc.items()
        }

    def match(self, query: str) -> list[str]:
        words = set(re.findall(r"[a-z]+", query.lower()))
        return sorted(d for d, tokens in self.distinctive.items() if tokens & words)


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    query_type: QueryType
    articles: list[str] = Field(default_factory=list)  # normalized article numbers, e.g. ["14", "21A"]
    article_refs: list[str] = Field(default_factory=list)  # with clauses, e.g. ["19(1)(g)"]
    case_document_ids: list[str] = Field(default_factory=list)
    case_titles: list[str] = Field(default_factory=list)
    names_unknown_case: bool = False  # "X v. Y" form, but no such judgment in the corpus
    case_terms: list[str] = Field(default_factory=list)  # distinctive name tokens of the named case(s)
    content_terms: list[str] = Field(default_factory=list)  # evidential terms, for coverage checks
    signals: list[str] = Field(default_factory=list)  # why it was classified this way


def extract_article_refs(query: str) -> tuple[list[str], list[str]]:
    articles, refs = [], []
    for block in _ARTICLE_REF.finditer(query):
        for num, clauses in _ARTICLE_NUM.findall(block.group(1)):
            article = num.upper()
            ref = article + re.sub(r"\s", "", clauses)
            if article not in articles:
                articles.append(article)
            if ref not in refs:
                refs.append(ref)
    return articles, refs


def analyze_query(query: str, cases: CaseIndex) -> QueryAnalysis:
    articles, refs = extract_article_refs(query)
    case_ids = cases.match(query)
    case_form = bool(_CASE_FORM.search(query))
    interpretation = _INTERPRETATION.search(query)
    signals = []
    if case_ids or case_form:
        query_type = QueryType.CASE
        signals.append(f"names case(s) {[cases.titles[d] for d in case_ids]}" if case_ids else "has 'X v. Y' case form")
    elif interpretation:
        query_type = QueryType.INTERPRETATION
        signals.append(f"interpretation cue {interpretation.group(0)!r}")
    elif articles:
        query_type = QueryType.PROVISION
        signals.append(f"cites Article(s) {articles} without interpretation cues")
    else:
        query_type = QueryType.GENERAL
        signals.append("no article, case or interpretation cue")
    if articles and query_type is not QueryType.PROVISION:
        signals.append(f"cites Article(s) {articles}")

    # A named case's own words (and the "v." marker) are not evidential content to look for.
    case_name_tokens = {t for d in case_ids for t in tokenize(cases.titles[d])} | {"v", "vs", "versus"}
    if case_form and not case_ids:
        case_name_tokens = set()  # unknown case: keep its words, they are all we can search for
    content = [t for t in dict.fromkeys(tokenize(query)) if t not in QUERY_STOPWORDS and t not in case_name_tokens]
    return QueryAnalysis(
        query=query, query_type=query_type, articles=articles, article_refs=refs,
        case_document_ids=case_ids, case_titles=[cases.titles[d] for d in case_ids],
        names_unknown_case=case_form and not case_ids, content_terms=content, signals=signals,
        case_terms=sorted({t for d in case_ids for t in cases.distinctive[d]}),
    )
