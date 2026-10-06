"""Claim-level evidence-support check (Phase 6; SRS FR-13 Basic Evidence Check; D26).

Answers one question only: does the retrieved evidence a claim cites *textually*
support it? It does not reason about the law, decide legal correctness, judge
whether a precedent is good law, or rank authorities. It never modifies evidence.

Decision, in order (the first rule that applies decides):

  1. structural   empty claim / no citation / unknown citation / empty evidence -> INSUFFICIENT
  2. quotations   text in quotation marks (4+ words) must be verbatim in the cited text -> else INSUFFICIENT
  3. exact match  the claim itself appears verbatim in the cited text                 -> SUPPORTED
  4. content      fewer than min_claim_terms key terms                                 -> INSUFFICIENT
  5. entities     names and numbers in the claim must appear in the cited text or its
                  metadata (title, case name, Article, year)                           -> else INSUFFICIENT
  6. contradiction explicit, pattern-based (see _contradiction)                        -> CONTRADICTED
  7. negation     a negative claim needs evidence negating the same terms              -> else INSUFFICIENT
  8. coverage     share of key terms found in the contributing evidence >= support_coverage
                  (semantic method: cosine >= semantic_floor too; or the paraphrase path:
                  coverage >= semantic_support_coverage and cosine >= semantic_support_threshold)
                                                                                      -> SUPPORTED
  9. otherwise                                                                         -> INSUFFICIENT

Similarity is not entailment: lexical/semantic overlap can only fail to confirm
support (INSUFFICIENT); CONTRADICTED requires an explicit pattern. The model-based
NLI verification of SRS V2 (FR-18 - FR-25) is a different, later component.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from constitutional_evidence_rag.common.config import EvidenceValidationSettings
from constitutional_evidence_rag.common.evidence import EvidenceItem
from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.common.validation import SupportStatus
from constitutional_evidence_rag.generation.answer import split_sentences
from constitutional_evidence_rag.generation.grounding import unverified_quotations
from constitutional_evidence_rag.retrieval.bm25 import STOPWORDS, tokenize

MARKER = re.compile(r"\[\s*E\d+(?:\s*[,;]\s*E?\d+)*\s*\]")
NEGATORS = frozenset({"not", "no", "never", "cannot", "nothing", "none", "neither", "nor"})
# Words that carry no checkable content in a claim: attribution, modality, question words.
GENERIC = frozenset(
    "article articles art constitution court courts supreme judgment judgments case cases held holds hold "
    "observed noted stated provides provide provided provision says said read reads interpreted interprets "
    "interpretation according evidence passage passages suggest suggests suggested together also therefore thus "
    "may must shall can could should would will does did do one any every whether per which while because "
    "under within upon about later earlier further first second".split()
) | STOPWORDS | NEGATORS
# Capitalized words that are not names to look for in the evidence.
GENERIC_CAPITALIZED = frozenset(
    "the a an in on this that these those it its article articles constitution court supreme high india indian state "
    "states union parliament government justice part schedule section clause act bench judgment petitioner respondent "
    "however read together thus also both such further according under every any all no".split()
)
ANTONYMS = [({"valid"}, {"invalid", "void"}), ({"constitutional"}, {"unconstitutional", "ultra"}),
            ({"upheld", "uphold", "upholds"}, {"struck", "strike", "quashed", "invalidated"}),
            ({"permitted", "permissible", "allowed", "lawful", "legal"}, {"prohibited", "impermissible", "forbidden", "unlawful", "illegal"})]
ABSOLUTE = {"absolute", "unlimited", "unrestricted", "unqualified", "unconditional"}
QUALIFIERS = ("subject to", "reasonable restriction", "except", "restriction")
_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ions", "ion", "ies", "ied", "ally", "ional", "ural",
             "ed", "es", "s", "ly")
_RESTORE = {"ural": "ure"}  # constitutional/constitution -> constitut, procedural/procedure -> procedur; personal stays


def stem(token: str) -> str:
    """Crude, deterministic suffix stripping so 'protects'/'protection', 'deprived'/'deprivation'
    and 'procedure'/'procedural' match. Tokens with digits (Articles, years) are kept whole."""
    if any(ch.isdigit() for ch in token) or len(token) <= 4:
        return token
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            token = token[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else _RESTORE.get(suffix, ""))
            break
    return token[:-1] if token.endswith("e") and len(token) > 4 else token


def _words(text: str) -> list[str]:
    """Ordered lower-case words (n't -> not), for negation and pattern analysis."""
    return re.findall(r"[a-z0-9()]+", re.sub(r"n['’]t\b", " not", text.lower()))


def content_terms(text: str) -> list[str]:
    return list(dict.fromkeys(stem(t) for t in tokenize(re.sub(r"n['’]t\b", " not", text)) if t not in GENERIC))


def _stems(text: str) -> set[str]:
    return {stem(t) for t in tokenize(text)}


def unquoted(text: str) -> str:
    """The claim without its quotations: verified verbatim quotes are the evidence's own words,
    so their negations are not the claim's assertions."""
    return re.sub(r"“[^”]*”|\"[^\"]*\"", " ", text)


def strip_markers(text: str) -> str:
    return re.sub(r"\s+([.;,?!])", r"\1", " ".join(MARKER.sub(" ", text).split())).strip()


@dataclass(frozen=True)
class SupportResult:
    status: SupportStatus
    score: float
    reason: str
    coverage: float | None = None
    semantic_score: float | None = None
    supporting_ids: list[str] = field(default_factory=list)


class SupportValidator:
    """Stateless apart from an optional embedder (semantic method), created lazily."""

    def __init__(self, settings: EvidenceValidationSettings, embedder_factory: Callable[[], object] | None = None):
        self.settings = settings
        self._embedder_factory = embedder_factory
        self._embedder = None

    @property
    def method(self) -> str:
        return self.settings.method

    # ------------------------------------------------------------------ public

    def validate(self, claim: str, citation_ids: Sequence[str], evidence: Mapping[str, EvidenceItem]) -> SupportResult:
        s = self.settings
        text = strip_markers(claim)
        if not re.search(r"\w", text):
            return _insufficient("The claim is empty.")
        ids = list(dict.fromkeys(citation_ids))
        if not ids:
            return _insufficient("The claim cites no evidence.")
        if missing := [i for i in ids if i not in evidence]:
            return _insufficient(f"The claim cites {', '.join(f'[{i}]' for i in missing)}, which is not among the retrieved evidence.")
        items = [evidence[i] for i in ids]
        texts = [it.chunk.text for it in items]
        if not any(t.strip() for t in texts):
            return _insufficient("The cited evidence contains no text.")
        if bad := unverified_quotations(text, texts):
            return _insufficient(f"Quoted text \"{bad[0][:60]}\" is not verbatim in the cited evidence.")

        canon_claim = _canonical(text)
        for item in items:
            if len(canon_claim.split()) >= 4 and canon_claim in _canonical(item.chunk.text):
                return SupportResult(SupportStatus.SUPPORTED, 1.0, f"The claim appears verbatim in [{item.evidence_id}].",
                                     coverage=1.0, supporting_ids=[item.evidence_id])

        terms = content_terms(text)
        if len(terms) < s.min_claim_terms:
            return _insufficient(f"The claim has too little checkable content ({len(terms)} key term(s)).")

        evidence_stems = {i.evidence_id: _stems(i.chunk.text) | _metadata_stems(i) for i in items}
        all_stems = set().union(*evidence_stems.values())
        if missing_entities := [e for e in _entities(text) if not ({stem(t) for t in tokenize(e)} <= all_stems)]:
            return _insufficient(f"The claim names {', '.join(repr(e) for e in missing_entities)}, which the cited evidence "
                                 f"({', '.join(f'[{i}]' for i in ids)}) does not mention.")

        if (conflict := _contradiction(unquoted(text), items, s.contradiction_min_overlap)) is not None:
            return SupportResult(SupportStatus.CONTRADICTED, 0.0, conflict, supporting_ids=[])

        if (negation := _negation_problem(unquoted(text), items, s.contradiction_min_overlap)) is not None:
            return _insufficient(negation)

        per_item = {i: sum(t in evidence_stems[i] for t in terms) / len(terms) for i in ids}
        contributing = [i for i in ids if per_item[i] >= s.contribution_min] or [max(ids, key=lambda i: per_item[i])]
        union = set().union(*(evidence_stems[i] for i in contributing))
        coverage = sum(t in union for t in terms) / len(terms)
        missing_terms = [t for t in terms if t not in union]

        semantic = None
        if s.method == "semantic":
            semantic = self._cosine(text, [evidence[i].chunk.text for i in contributing])
        lexical_ok = coverage >= s.support_coverage and (semantic is None or semantic >= s.semantic_floor)
        paraphrase_ok = semantic is not None and coverage >= s.semantic_support_coverage and semantic >= s.semantic_support_threshold
        score = coverage if semantic is None else (coverage + max(semantic, 0.0)) / 2
        detail = f"{round(coverage * len(terms))}/{len(terms)} key terms found in {', '.join(f'[{i}]' for i in contributing)}" + \
                 (f"; semantic similarity {semantic:.2f}" if semantic is not None else "")
        if lexical_ok or paraphrase_ok:
            return SupportResult(SupportStatus.SUPPORTED, round(score, 4), f"Supported: {detail}.", coverage, semantic, contributing)
        why = f"below the {s.support_coverage:.0%} coverage threshold" if coverage < s.support_coverage else \
              f"semantic similarity below {s.semantic_floor}"
        missing_note = f" Not found: {', '.join(_shown(missing_terms[:6], text))}." if missing_terms else ""
        return SupportResult(SupportStatus.INSUFFICIENT, round(score, 4), f"Not confirmed ({why}): {detail}.{missing_note}",
                             coverage, semantic, contributing)

    # ------------------------------------------------------------------ semantic

    def _cosine(self, claim: str, passages: list[str]) -> float:
        if self._embedder is None:
            if self._embedder_factory is None:
                raise RuntimeError("evidence_validation.method is 'semantic' but no embedding model is available")
            self._embedder = self._embedder_factory()
        q = np.asarray(self._embedder.embed_query(claim), dtype=np.float32)
        docs = np.asarray(self._embedder.embed_documents(passages), dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        docs = docs / np.where(np.linalg.norm(docs, axis=1, keepdims=True) == 0, 1.0, np.linalg.norm(docs, axis=1, keepdims=True))
        return float(np.max(docs @ q))


# ---------------------------------------------------------------------- helpers


def _shown(stems, claim: str) -> list[str]:
    """Display stems as the words the claim actually used."""
    original = {}
    for w in _words(claim):
        original.setdefault(stem(w), w)
    return [original.get(t, t) for t in stems]


def _insufficient(reason: str) -> SupportResult:
    return SupportResult(SupportStatus.INSUFFICIENT, 0.0, reason)


def _canonical(text: str) -> str:
    text = re.sub(r"[“”\"‘’'`]", "", text.lower())
    return " ".join(re.sub(r"[^\w()]+", " ", text).split())


def _metadata_stems(item: EvidenceItem) -> set[str]:
    c = item.chunk
    parts = [item.document_title, c.case_name or "", c.article_title or "", c.heading or ""]
    if c.article_number:
        parts.append(f"article {c.article_number}")
    if item.source_date and c.source_type is SourceType.JUDGMENT:
        parts.append(str(item.source_date.year))
    return _stems(" ".join(parts))


def _entities(text: str) -> list[str]:
    """Numbers / legal references, and capitalized names (a sentence-initial word counts only
    when it starts a multi-word name, e.g. 'Maneka Gandhi')."""
    found = [t for t in dict.fromkeys(tokenize(text)) if any(ch.isdigit() for ch in t) and "(" not in t]
    for sentence in split_sentences(text):
        words = re.findall(r"[A-Za-z][\w’'-]*", sentence)
        for i, w in enumerate(words):
            if not w[0].isupper() or w.lower().strip("’'") in GENERIC_CAPITALIZED or len(w) < 3:
                continue
            if i == 0 and not (len(words) > 1 and words[1][0].isupper() and words[1].lower() not in GENERIC_CAPITALIZED):
                continue
            found.append(w)
    return list(dict.fromkeys(found))


def _evidence_sentences(item: EvidenceItem) -> list[str]:
    sentences = split_sentences(item.chunk.text)
    if item.chunk.source_type is SourceType.CONSTITUTIONAL_TEXT and item.chunk.article_title:
        sentences.append(item.chunk.article_title)
    return sentences


def _overlap(claim_terms: set[str], sentence: str, context: set[str] = frozenset()) -> float:
    """Share of claim terms in the sentence. `context` adds the item's own metadata (its Article
    number and title, case name): a sentence inside Article 21's text is about Article 21 even
    when the splitter has separated it from the "21." heading."""
    return len(claim_terms & (_stems(sentence) | context)) / len(claim_terms) if claim_terms else 0.0


def _negated_terms(words: list[str]) -> set[str]:
    out = set()
    for i, w in enumerate(words):
        if w in NEGATORS:
            out |= {stem(x) for x in words[i + 1:i + 5] if x not in GENERIC}
    return out


def _contradiction(claim: str, items: Sequence[EvidenceItem], min_overlap: float) -> str | None:
    """Explicit conflict patterns only; anything else is left to the (conservative) support rules.
    Each pattern also requires the evidence sentence to share the claim's subject (min_overlap)."""
    words = _words(claim)
    word_set = set(words)
    claim_terms = set(content_terms(claim))
    for item in items:
        tag = f"[{item.evidence_id}]"
        meta = _metadata_stems(item)
        for sentence in _evidence_sentences(item):
            low = sentence.lower()
            s_words = set(_words(sentence))
            # P1: claim says something happens "without" X; evidence permits it only "except according to / only by" X
            if "without" in word_set:
                after = {stem(w) for w in words[words.index("without") + 1:words.index("without") + 5] if w not in GENERIC}
                rest = claim_terms - after - {"without"}
                guard = re.search(r"\b(except|only)\b(.{0,80})", low)
                if guard and after & _stems(guard.group(2)) and _overlap(rest, sentence, meta) >= min_overlap:
                    return (f"Contradicted: the claim says this can happen without {' '.join(_shown(sorted(after), claim))}, but {tag} allows it "
                            f"only \"{guard.group(0).strip()[:70]}\".")
            # P2: antonymous outcome words about the same subject (valid / invalid, upheld / struck down, ...)
            for side_a, side_b in ANTONYMS:
                for mine, theirs in ((side_a, side_b), (side_b, side_a)):
                    if word_set & mine and s_words & theirs and not s_words & mine:
                        rest = claim_terms - {stem(w) for w in mine | theirs}
                        if _overlap(rest, sentence, meta) >= min_overlap:
                            return (f"Contradicted: the claim says '{sorted(word_set & mine)[0]}' but {tag} says "
                                    f"'{sorted(s_words & theirs)[0]}' about the same subject.")
            # P3: claim calls a right absolute/unlimited; evidence makes it subject to restrictions/exceptions
            absolute = [w for w in ABSOLUTE if w in word_set]
            if absolute and not any(n in words[max(0, words.index(absolute[0]) - 3):words.index(absolute[0])] for n in NEGATORS):
                if any(q in low for q in QUALIFIERS) and _overlap(claim_terms - {stem(absolute[0])}, sentence, meta) >= min_overlap:
                    return f"Contradicted: the claim calls it '{absolute[0]}', but {tag} makes it subject to restrictions or exceptions."
            # P4: claim negates a term that the evidence states positively (in a sentence with no negation)
            negated = _negated_terms(words)
            if negated and not s_words & NEGATORS and "without" not in s_words:
                hit = negated & _stems(sentence)
                if hit and _overlap(claim_terms, sentence, meta) >= min_overlap:
                    return f"Contradicted: the claim denies '{_shown(sorted(hit), claim)[0]}', which {tag} states affirmatively."
    return None


def _negation_problem(claim: str, items: Sequence[EvidenceItem], min_overlap: float) -> str | None:
    """A negative claim is only confirmable if an evidence sentence about the same subject
    (min_overlap of the claim's terms, so compound claims work) negates the same terms."""
    negated = _negated_terms(_words(claim))
    if not negated:
        return None
    claim_terms = set(content_terms(claim))
    for item in items:
        meta = _metadata_stems(item)
        for sentence in _evidence_sentences(item):
            if _overlap(claim_terms, sentence, meta) >= min_overlap and negated & _negated_terms(_words(sentence)):
                return None
    return "The claim is negative, and no cited passage about the same subject negates the same terms; support cannot be confirmed."
