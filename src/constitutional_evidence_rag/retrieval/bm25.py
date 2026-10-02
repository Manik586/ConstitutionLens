"""BM25 sparse index (SRS FR-06; docs/DECISIONS.md D18).

Tokenization (shared by indexing and querying, so both sides always agree):
* NFKC-normalized, lower-cased; words are runs of Unicode letters/digits.
* Legal references stay whole (FR-06): "19(2)", "19(1)(g)", "243ZD(1)". Each
  reference also emits its shorter prefixes ("19(1)(g)" -> "19(1)(g)", "19(1)",
  "19"), so a query for "Article 19" still matches a chunk citing 19(1)(g),
  while a query for "19(2)" ranks chunks citing exactly 19(2) highest.
* OCR digit confusions seen in the SCR scans (Phase 1 analysis) are normalized
  on both sides: a token mixing digits with l/i/o ("l996", "2l") reads them as
  1/1/0, and a lone "l"/"i" in the first clause bracket of a multi-part reference
  ("19(l)(g)") reads as "1". "2(l)" alone is left as is: lettered clauses exist.
  The stored chunk text is never changed — only the index terms.
* A short list of English function words is dropped. No stemming.

Storage (<indexes_root>/<corpus_id>/bm25/, all .npy without pickle, so builds
are byte-for-byte reproducible): sorted vocabulary, CSR-style postings
(offsets / doc_ids / tfs), per-chunk token counts, chunk_order.npy, meta.json.
Raw statistics are stored and k1/b are applied at query time, so changing them
in the config needs no rebuild.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from constitutional_evidence_rag.common.chunks import Chunk
from constitutional_evidence_rag.retrieval.store import (
    IndexNotFoundError,
    RetrievalIndexError,
    StagedIndexDir,
    chunk_fingerprint,
    load_array,
    load_chunk_order,
    read_meta,
    save_array,
    save_chunk_order,
    validate_top_n,
    write_meta,
)

FORMAT_VERSION = 1
TOKENIZER_VERSION = 1

STOPWORDS = frozenset(
    "a an and are as at be been but by for from had has have he her his i if in into is it its "
    "of on or she so such than that the their them then there these they this those to was "
    "were what when where which who whom will with would".split()
)

_REFERENCE = r"\d+[a-z]{0,3}\s?(?:\(\s?[0-9a-z]{1,4}\s?\))+"  # 19(2), 19(1)(g), 243zd(1), "19 (2)"
_TOKEN = re.compile(rf"{_REFERENCE}|[^\W_]+")  # words: Unicode letters/digits, so accented or non-Latin text is not dropped
_OCR_NUMBER = re.compile(r"(?<![a-z0-9])(?=[0-9lio]*[0-9])[0-9lio]{2,}(?![a-z0-9])")
_OCR_FIRST_CLAUSE = re.compile(r"(\d+[a-z]{0,3}\s?)\(\s?[li]\s?\)(?=\s?\()")


def tokenize(text: str) -> list[str]:
    """Index/query terms for a text. Deterministic; order follows the text."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _OCR_NUMBER.sub(lambda m: m.group(0).replace("l", "1").replace("i", "1").replace("o", "0"), text)
    text = _OCR_FIRST_CLAUSE.sub(r"\1(1)", text)
    tokens: list[str] = []
    for match in _TOKEN.finditer(text):
        token = re.sub(r"\s", "", match.group(0))
        if "(" in token:
            base = re.match(r"\d+[a-z]{0,3}", token).group(0)
            parts = re.findall(r"\([0-9a-z]+\)", token)
            tokens.append(base)
            tokens.extend(base + "".join(parts[: i + 1]) for i in range(len(parts)))
        elif token not in STOPWORDS:
            tokens.append(token)
    return tokens


@dataclass
class BM25Index:
    """Postings over chunk rows; row i is chunk_ids[i]."""

    chunk_ids: list[str]
    terms: np.ndarray  # sorted unicode vocabulary
    offsets: np.ndarray  # int64, len(terms) + 1: postings of term t are [offsets[t], offsets[t+1])
    doc_ids: np.ndarray  # int32 row numbers
    tfs: np.ndarray  # int32 term frequencies
    doc_lengths: np.ndarray  # int32 tokens per chunk
    _term_index: dict[str, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._term_index = {str(t): i for i, t in enumerate(self.terms.tolist())}
        self._avgdl = float(self.doc_lengths.mean()) if len(self.doc_lengths) else 0.0

    # ---------------------------------------------------------------- build / persist

    @classmethod
    def build(cls, chunks: Sequence[Chunk]) -> BM25Index:
        if not chunks:
            raise RetrievalIndexError("cannot build a BM25 index from zero chunks")
        postings: dict[str, list[tuple[int, int]]] = {}
        lengths = []
        for row, chunk in enumerate(chunks):
            counts = Counter(tokenize(chunk.text))
            lengths.append(sum(counts.values()))
            for term, tf in counts.items():
                postings.setdefault(term, []).append((row, tf))
        terms = sorted(postings)
        offsets = np.zeros(len(terms) + 1, dtype=np.int64)
        doc_ids, tfs = [], []
        for i, term in enumerate(terms):
            plist = postings[term]  # rows are appended in increasing order
            doc_ids.extend(r for r, _ in plist)
            tfs.extend(tf for _, tf in plist)
            offsets[i + 1] = offsets[i] + len(plist)
        return cls(
            chunk_ids=[c.chunk_id for c in chunks],
            terms=np.array(terms, dtype=str),
            offsets=offsets,
            doc_ids=np.array(doc_ids, dtype=np.int32),
            tfs=np.array(tfs, dtype=np.int32),
            doc_lengths=np.array(lengths, dtype=np.int32),
        )

    def save(self, directory: Path) -> None:
        with StagedIndexDir(directory) as tmp:
            for name, array in [("terms.npy", self.terms), ("offsets.npy", self.offsets),
                                ("doc_ids.npy", self.doc_ids), ("tfs.npy", self.tfs),
                                ("doc_lengths.npy", self.doc_lengths)]:
                save_array(tmp, name, array)
            save_chunk_order(tmp, self.chunk_ids)
            write_meta(tmp, {
                "kind": "bm25",
                "format_version": FORMAT_VERSION,
                "tokenizer_version": TOKENIZER_VERSION,
                "n_chunks": len(self.chunk_ids),
                "vocab_size": int(len(self.terms)),
                "chunks_fingerprint": chunk_fingerprint(self.chunk_ids),
            })

    @classmethod
    def load(cls, directory: Path) -> BM25Index:
        meta = read_meta(directory)
        if meta.get("kind") != "bm25" or meta.get("tokenizer_version") != TOKENIZER_VERSION:
            raise IndexNotFoundError(f"{directory} holds an incompatible BM25 index ({meta}) — rebuild it")
        index = cls(
            chunk_ids=load_chunk_order(directory),
            terms=load_array(directory, "terms.npy"),
            offsets=load_array(directory, "offsets.npy"),
            doc_ids=load_array(directory, "doc_ids.npy"),
            tfs=load_array(directory, "tfs.npy"),
            doc_lengths=load_array(directory, "doc_lengths.npy"),
        )
        if (len(index.chunk_ids) != len(index.doc_lengths) or len(index.offsets) != len(index.terms) + 1
                or index.offsets[-1] != len(index.doc_ids) or len(index.doc_ids) != len(index.tfs)):
            raise RetrievalIndexError(f"BM25 index at {directory} is internally inconsistent — rebuild it")
        return index

    # ---------------------------------------------------------------- query

    @property
    def vocab_size(self) -> int:
        return len(self.terms)

    def search(self, query: str, top_n: int, *, k1: float = 1.5, b: float = 0.75) -> list[tuple[int, float]]:
        """(row, score) pairs, best first; ties broken by row (= chunks.jsonl order).
        A query with no indexed terms returns [] (not an error)."""
        top_n = validate_top_n(top_n)
        n = len(self.chunk_ids)
        scores = np.zeros(n, dtype=np.float64)
        norm = k1 * (1.0 - b + b * self.doc_lengths / self._avgdl)
        # sorted, de-duplicated terms: a fixed summation order keeps scores bit-for-bit reproducible
        for term in sorted(set(tokenize(query))):
            t = self._term_index.get(term)
            if t is None:
                continue
            start, end = self.offsets[t], self.offsets[t + 1]
            rows, tf = self.doc_ids[start:end], self.tfs[start:end].astype(np.float64)
            df = end - start
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            scores[rows] += idf * tf * (k1 + 1.0) / (tf + norm[rows])
        hits = np.flatnonzero(scores > 0)
        if hits.size == 0:
            return []
        order = np.lexsort((hits, -scores[hits]))[:top_n]
        return [(int(hits[i]), float(scores[hits[i]])) for i in order]
