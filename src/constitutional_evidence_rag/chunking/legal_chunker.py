"""Legal-aware chunking (SRS FR-03, NFR-03; docs/DECISIONS.md D16).

    documents.jsonl + metadata.jsonl  (Phase 1, one corpus)
        -> current version of each document (replaced_by is null)
        -> structure.document_lines / detect_sections
        -> pack each section into chunks          (never across a section boundary)
        -> data/processed/<corpus_id>/chunks.jsonl

Packing rules, per section:
* A section within `max_tokens` is one chunk (whole article, whole headed section).
* A longer section is split at line boundaries, preferring a line that starts a clause
  "(2)" or a numbered paragraph, or that follows a sentence end. No chunk exceeds
  `max_tokens` (FR-03 acceptance); a single line longer than that is split by words.
* Consecutive chunks of the same section overlap by up to `overlap_tokens`, whole lines
  only. There is no overlap across sections, so no chunk mixes two articles.

Chunk IDs are deterministic: "<document_id>@v<version>:<index>:<sha256(text)[:8]>". The
same input and settings give the same IDs; a change in the text changes the hash, so a
stale reference (e.g. in an evaluation set) fails visibly instead of pointing at
different text.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from constitutional_evidence_rag.common.chunks import Chunk, ChunkingMethod
from constitutional_evidence_rag.common.config import ChunkingSettings
from constitutional_evidence_rag.common.logging import get_logger
from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType
from constitutional_evidence_rag.common.pages import ParsedDocument
from constitutional_evidence_rag.chunking.structure import Line, Section, detect_sections, document_lines
from constitutional_evidence_rag.ingestion.metadata import load_registry, write_jsonl_atomic
from constitutional_evidence_rag.ingestion.pipeline import (
    DOCUMENTS_FILENAME,
    METADATA_FILENAME,
    corpus_dir,
    load_parsed_documents,
)

logger = get_logger(__name__)

CHUNKS_FILENAME = "chunks.jsonl"

_TOKEN = re.compile(r"\w+|[^\w\s]")
_SENTENCE_END = re.compile(r"[.;:?!][\"'”’)\]]*$")
_PARAGRAPH_START = re.compile(r"^\d{1,3}\.\s+[A-Z]")
_CLAUSE_START = re.compile(r"^\(\d+[A-Z]?\)\s")


class ChunkingError(Exception):
    """The corpus has no Phase 1 output to chunk, or it is inconsistent."""


def count_tokens(text: str) -> int:
    """Approximate token count: words and individual punctuation marks (D16).

    Model-independent on purpose: no embedding model is chosen until Phase 3.
    Subword tokenizers typically produce somewhat more tokens than this for
    English, which the target/max headroom absorbs.
    """
    return len(_TOKEN.findall(text))


# ------------------------------------------------------------------ one document


@dataclass(frozen=True)
class _Unit:
    line: Line
    tokens: int
    clause: str | None  # clause label if this unit starts a top-level clause
    prefer_break_before: bool


def chunk_document(
    document: ParsedDocument, metadata: DocumentMetadata, settings: ChunkingSettings
) -> list[Chunk]:
    """Chunk one parsed document version. Deterministic for fixed inputs and settings."""
    if (metadata.document_id, metadata.document_version) != (document.document_id, document.document_version):
        raise ChunkingError(
            f"metadata {metadata.document_id}@v{metadata.document_version} does not match "
            f"document {document.document_id}@v{document.document_version}"
        )
    labels = {p.provenance.page_number: p.page_label for p in document.pages}
    sections = detect_sections(document_lines(document), document.source_type)

    chunks: list[Chunk] = []
    for section in sections:
        units = _units(section, settings.max_tokens)
        if not units:
            continue
        total = sum(u.tokens for u in units)
        if total <= settings.max_tokens:
            pieces = [(0, len(units), 0)]
        else:
            pieces = _pack(units, settings)
        for start, end, fresh in pieces:
            chunks.append(_make_chunk(document, metadata, section, units, start, end, fresh,
                                      split=len(pieces) > 1, index=len(chunks), labels=labels))
    return chunks


def _units(section: Section, max_tokens: int) -> list[_Unit]:
    units: list[_Unit] = []
    for i, line in enumerate(section.lines):
        tokens = count_tokens(line.text)
        if tokens == 0:
            continue
        clause = section.clause_starts.get(i)
        prefer = bool(clause or _PARAGRAPH_START.match(line.text) or _CLAUSE_START.match(line.text)) or (
            bool(units) and bool(_SENTENCE_END.search(units[-1].line.text))
        )
        if tokens <= max_tokens:
            units.append(_Unit(line, tokens, clause, prefer))
            continue
        # A single over-long line: split by words so no chunk can exceed max_tokens.
        words, piece = line.text.split(), []
        for word in words:
            if piece and count_tokens(" ".join(piece + [word])) > max_tokens:
                units.append(_Unit(Line(line.page, " ".join(piece)), count_tokens(" ".join(piece)), clause, prefer))
                clause, prefer, piece = None, False, []
            piece.append(word)
        if piece:
            units.append(_Unit(Line(line.page, " ".join(piece)), count_tokens(" ".join(piece)), clause, prefer))
    return units


def _pack(units: list[_Unit], settings: ChunkingSettings) -> list[tuple[int, int, int]]:
    """Split units into (start, end, fresh_start) pieces; units[start:fresh_start] is overlap."""
    target, max_tokens, overlap = settings.target_tokens, settings.max_tokens, settings.overlap_tokens
    prefix = [0]
    for u in units:
        prefix.append(prefix[-1] + u.tokens)

    def size(a: int, b: int) -> int:
        return prefix[b] - prefix[a]

    n, start, fresh, pieces = len(units), 0, 0, []
    while start < n:
        if size(start, n) <= max_tokens:
            pieces.append((start, n, fresh))
            break
        end = start + 1
        while end < n and size(start, end + 1) <= target:
            end += 1
        # Prefer the latest clean break at or before `end` that still fills half the target...
        cut = next((k for k in range(end, start, -1) if units[k].prefer_break_before and size(start, k) >= target // 2), None) if end < n else None
        if cut is None:
            # ...otherwise look ahead for one before max_tokens, else cut at `end`.
            cut, k = end, end
            while k < n and size(start, k + 1) <= max_tokens:
                k += 1
                if k < n and units[k].prefer_break_before:
                    cut = k
                    break
        pieces.append((start, cut, fresh))
        next_start = cut
        while next_start - 1 > start and size(next_start - 1, cut) <= overlap:
            next_start -= 1
        start, fresh = next_start, cut
    return pieces


def _make_chunk(document, metadata, section, units, start, end, fresh, *, split, index, labels) -> Chunk:
    body = units[start:end]
    text = "\n".join(u.line.text for u in body)
    pages = [u.line.page for u in body]
    method = (
        ChunkingMethod.FALLBACK if not section.structural
        else ChunkingMethod.STRUCTURE_SPLIT if split else ChunkingMethod.STRUCTURE
    )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return Chunk(
        chunk_id=f"{document.document_id}@v{document.document_version}:{index:04d}:{digest}",
        corpus_id=document.corpus_id,
        document_id=document.document_id,
        document_version=document.document_version,
        source_type=document.source_type,
        source_url=metadata.source_url,
        chunk_index=index,
        page_start=min(pages),
        page_end=max(pages),
        page_label_start=labels.get(min(pages)),
        page_label_end=labels.get(max(pages)),
        text=text,
        token_count=sum(u.tokens for u in body),
        chunking_method=method,
        division=section.division,
        part=section.part,
        chapter=section.chapter,
        article_number=section.article_number,
        article_title=section.article_title,
        clause_labels=[u.clause for u in units[max(start, fresh):end] if u.clause],
        heading=section.heading,
        opinion_author=section.opinion_author,
        case_name=metadata.title if document.source_type is SourceType.JUDGMENT else None,
        paragraph_number=None,
    )


# ------------------------------------------------------------------ one corpus


@dataclass
class ChunkingReport:
    documents: int = 0
    chunks: int = 0
    methods: Counter = field(default_factory=Counter)
    per_document: dict[str, int] = field(default_factory=dict)


def chunk_corpus(processed_root: Path, corpus_id: str, settings: ChunkingSettings) -> ChunkingReport:
    """Chunk the current version of every document in one corpus and rewrite its chunks.jsonl.

    chunks.jsonl is derived data: it is regenerated in full, atomically, from Phase 1 output.
    Superseded document versions stay in documents.jsonl for provenance, but are not chunked,
    so retrieval never returns two versions of the same source.
    """
    target = corpus_dir(processed_root, corpus_id)
    metadata_path = target / METADATA_FILENAME
    if not metadata_path.exists():
        raise ChunkingError(f"No Phase 1 output for corpus {corpus_id!r} at {target} — run ingestion first")
    current = {(r.document_id, r.document_version): r for r in load_registry(metadata_path) if r.replaced_by is None}
    documents = [d for d in load_parsed_documents(target / DOCUMENTS_FILENAME)
                 if (d.document_id, d.document_version) in current]
    missing = set(current) - {(d.document_id, d.document_version) for d in documents}
    if missing:
        raise ChunkingError(f"Registered versions missing from {DOCUMENTS_FILENAME}: {sorted(missing)}")

    report, all_chunks = ChunkingReport(), []
    for document in sorted(documents, key=lambda d: d.document_id):
        chunks = chunk_document(document, current[(document.document_id, document.document_version)], settings)
        all_chunks.extend(chunks)
        report.documents += 1
        report.per_document[document.document_id] = len(chunks)
        report.methods.update(c.chunking_method.value for c in chunks)
        logger.info("Chunked %s@v%d: %d chunks", document.document_id, document.document_version, len(chunks))
    report.chunks = len(all_chunks)
    write_jsonl_atomic(target / CHUNKS_FILENAME, (c.model_dump_json() for c in all_chunks))
    logger.info("Chunking of %s complete: %d documents, %d chunks", corpus_id, report.documents, report.chunks)
    return report


def load_chunks(path: Path) -> list[Chunk]:
    """Read and validate chunks.jsonl (for Phase 3 indexing and tests)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]
