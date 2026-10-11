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
actually in the corpus, using tokens unique to one case. A name token misspelled by
one edit ("maenka" for "maneka", "kesavanada" for "kesavananda") still resolves; the
correction is recorded and reported, never silent. With a reference index (the curated
library), a question naming a known case that is not in this corpus is flagged.
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
    "establish established significance significant case cases judgment judgments law laws "
    # question framing and function words: they say how the question is asked, not what to find
    "argue argued argues arguing argument arguments contend contended contention can could would should may might must "
    "will shall not no yes any some there their them they it its this that these those which who whom whose when where "
    "why whether also than then very against down up away took take taken give given lay laid put get got make made "
    "apply applies applied".split()
)
# Name parts too common to identify one case.
_GENERIC_NAME_TOKENS = frozenset(
    "his holiness and ors anr others another etc the of ltd limited state states union india dead lrs by through thr "
    "secretary ministry law justice government govt citizens welfare forum industries mills foundation company society "
    "association council board corporation co pvt private public commissioner director collector".split()
)


def _osa_distance(a: str, b: str) -> int:
    """Optimal string alignment distance (Levenshtein plus adjacent transpositions)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def is_one_typo(word: str, name: str) -> bool:
    """`word` is `name` with one typo: one insertion, deletion, substitution or swap of
    adjacent letters. Conservative: both at least 6 letters, same first letter, and not
    an inflection (one a prefix of the other, e.g. "reserved" / "reserve")."""
    if word == name or len(word) < 6 or len(name) < 6 or word[0] != name[0] or abs(len(word) - len(name)) > 1:
        return False
    if word.startswith(name) or name.startswith(word):
        return False
    return _osa_distance(word, name) == 1


_SUFFIXES = ("ing", "ied", "ies", "ed", "es", "s")


def light_stem(token: str) -> str:
    """Crude English suffix stripping for coverage checks only ("argued" ~ "argue",
    "rivers" ~ "river", "discharging" ~ "discharge"). Never used for indexing."""
    t = token
    if len(t) > 4 and not t[-1].isdigit():
        for suffix in _SUFFIXES:
            if t.endswith(suffix) and len(t) - len(suffix) >= 3 and not (suffix == "s" and t.endswith("ss")):
                t = t[: -len(suffix)] + ("y" if suffix in ("ied", "ies") else "")
                break
    return t[:-1] if len(t) > 4 and t.endswith("e") else t


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
        return self.match_with_corrections(query)[0]

    def match_with_corrections(self, query: str) -> tuple[list[str], dict[str, str]]:
        """Document ids of the cases the query names, and {misspelled word: name token} for
        words that matched only through a one-letter typo."""
        words = set(re.findall(r"[a-z]+", query.lower()))
        matched, corrections = set(), {}
        for d, tokens in self.distinctive.items():
            if tokens & words:
                matched.add(d)
        for word in sorted(words):
            if any(word in tokens for tokens in self.distinctive.values()):
                continue
            for d, tokens in self.distinctive.items():
                name = next((t for t in sorted(tokens) if is_one_typo(word, t)), None)
                if name:
                    matched.add(d)
                    corrections[word] = name
        return sorted(matched), corrections


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    query_type: QueryType
    articles: list[str] = Field(default_factory=list)  # normalized article numbers, e.g. ["14", "21A"]
    article_refs: list[str] = Field(default_factory=list)  # with clauses, e.g. ["19(1)(g)"]
    case_document_ids: list[str] = Field(default_factory=list)
    case_titles: list[str] = Field(default_factory=list)
    names_unknown_case: bool = False  # names a case that is not in this corpus ("X v. Y" form, or a known library case)
    unknown_case_titles: list[str] = Field(default_factory=list)  # titles of known cases missing from this corpus
    corrections: dict[str, str] = Field(default_factory=dict)  # misspelled word -> case-name token it was read as
    search_query: str | None = None  # the query with corrections applied, used for retrieval
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


def analyze_query(query: str, cases: CaseIndex, reference: CaseIndex | None = None) -> QueryAnalysis:
    """`reference`: an index of known judgments outside this corpus (the curated library),
    used only to notice that a question names a case this corpus does not contain."""
    articles, refs = extract_article_refs(query)
    case_ids, corrections = cases.match_with_corrections(query)
    unknown_titles: list[str] = []
    if not case_ids and reference is not None:
        ref_ids, ref_corrections = reference.match_with_corrections(query)
        unknown_titles = [reference.titles[d] for d in ref_ids]
        corrections = {**corrections, **ref_corrections}
    case_form = bool(_CASE_FORM.search(query))
    interpretation = _INTERPRETATION.search(query)
    signals = []
    if case_ids or case_form or unknown_titles:
        query_type = QueryType.CASE
        signals.append(f"names case(s) {[cases.titles[d] for d in case_ids]}" if case_ids
                       else f"names case(s) not in this corpus {unknown_titles}" if unknown_titles else "has 'X v. Y' case form")
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

    if corrections:
        signals.append("spelling: " + ", ".join(f"{w!r} read as {n!r}" for w, n in sorted(corrections.items())))

    # A named case's own words (and the "v." marker) are not evidential content to look for,
    # nor are misspellings of them.
    case_name_tokens = {t for d in case_ids for t in tokenize(cases.titles[d])} | {"v", "vs", "versus"} | set(corrections)
    if unknown_titles:
        case_name_tokens |= {t for title in unknown_titles for t in tokenize(title)}
    if case_form and not case_ids and not unknown_titles:
        case_name_tokens = set()  # unknown case: keep its words, they are all we can search for
    content = [t for t in dict.fromkeys(tokenize(query)) if t not in QUERY_STOPWORDS and t not in case_name_tokens]
    search_query = query
    for word, name in corrections.items():
        search_query = re.sub(r"\b" + re.escape(word) + r"\b", name, search_query, flags=re.I)
    return QueryAnalysis(
        query=query, query_type=query_type, articles=articles, article_refs=refs,
        case_document_ids=case_ids, case_titles=[cases.titles[d] for d in case_ids],
        names_unknown_case=(case_form and not case_ids) or bool(unknown_titles), unknown_case_titles=unknown_titles,
        corrections=corrections, search_query=search_query if corrections else None,
        content_terms=content, signals=signals,
        case_terms=sorted({t for d in case_ids for t in cases.distinctive[d]}),
    )
