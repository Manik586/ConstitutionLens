"""Fixtures for Phase 3 tests: a small corpus of real-shaped Chunks and a
deterministic embedder, so no model weights are ever downloaded."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import yaml

from constitutional_evidence_rag.chunking.legal_chunker import CHUNKS_FILENAME, count_tokens
from constitutional_evidence_rag.common.chunks import Chunk, ChunkingMethod, Division
from constitutional_evidence_rag.common.config import AppSettings, LoggingSettings, PathsSettings, RetrievalSettings, Settings
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, SourceType
from constitutional_evidence_rag.ingestion.pipeline import corpus_dir

REPO_ROOT = Path(__file__).resolve().parents[3]

# (document_id, source_type, text, extra metadata)
CORPUS = [
    ("CONST-000000000001", SourceType.CONSTITUTIONAL_TEXT,
     "14. Equality before law.—The State shall not deny to any person equality before the law or the "
     "equal protection of the laws within the territory of India.", {"article_number": "14", "article_title": "Equality before law"}),
    ("CONST-000000000001", SourceType.CONSTITUTIONAL_TEXT,
     "(2) Nothing in sub-clause (a) of clause (1) shall affect the operation of any existing law imposing "
     "reasonable restrictions on the exercise of the right.", {"article_number": "19", "article_title": "Protection of certain rights"}),
    ("CONST-000000000001", SourceType.CONSTITUTIONAL_TEXT,
     "21. Protection of life and personal liberty.—No person shall be deprived of his life or personal "
     "liberty except according to procedure established by law.", {"article_number": "21", "article_title": "Protection of life and personal liberty"}),
    ("JUDG-0000000000aa", SourceType.JUDGMENT,
     "The restrictions under Article 19(2) must be reasonable restrictions on free speech and expression.",
     {"case_name": "Speech Case v. Union of India", "opinion_author": "NARIMAN, J.", "division": Division.OPINION}),
    ("JUDG-0000000000aa", SourceType.JUDGMENT,
     "The petitioner argued that section 66A of the Information Technology Act is vague and overbroad.",
     {"case_name": "Speech Case v. Union of India", "opinion_author": "NARIMAN, J.", "division": Division.OPINION}),
    ("JUDG-0000000000bb", SourceType.JUDGMENT,
     "Sexual harassment at the workplace violates Articles 14, 15 and 21; guidelines are laid down for employers.",
     {"case_name": "Workplace Case v. State", "heading": "HELD", "division": Division.FRONT_MATTER}),
    ("JUDG-0000000000bb", SourceType.JUDGMENT,
     "The guidelines shall be binding and enforceable in law until suitable legislation is enacted.",
     {"case_name": "Workplace Case v. State", "opinion_author": "VERMA, CJI.", "division": Division.OPINION}),
    ("JUDG-0000000000cc", SourceType.JUDGMENT,
     "The basic structure doctrine limits the amending power of Parliament under Article 368.",
     {"case_name": "Amendment Case v. State", "opinion_author": "SIKRI, C.J.", "division": Division.OPINION}),
]


def make_chunks(corpus=CORPUS, corpus_id: str = CURATED_CORPUS_ID, text_suffix: str = "") -> list[Chunk]:
    chunks, per_doc = [], {}
    for document_id, source_type, text, extra in corpus:
        text = text + text_suffix
        index = per_doc.get(document_id, 0)
        per_doc[document_id] = index + 1
        page = 10 + len(chunks)
        chunks.append(Chunk(
            chunk_id=f"{document_id}@v1:{index:04d}:{hashlib.sha256(text.encode()).hexdigest()[:8]}",
            corpus_id=corpus_id, document_id=document_id, document_version=1, source_type=source_type,
            source_url=f"https://example.org/{document_id}", chunk_index=index, page_start=page, page_end=page + 1,
            text=text, token_count=count_tokens(text), chunking_method=ChunkingMethod.STRUCTURE, **extra,
        ))
    return chunks


def write_chunks(processed_root: Path, chunks: list[Chunk], corpus_id: str = CURATED_CORPUS_ID) -> Path:
    target = corpus_dir(processed_root, corpus_id)
    target.mkdir(parents=True, exist_ok=True)
    path = target / CHUNKS_FILENAME
    path.write_text("".join(c.model_dump_json() + "\n" for c in chunks), encoding="utf-8")
    return path


def make_settings(tmp_path: Path, **retrieval) -> Settings:
    return Settings(
        app=AppSettings(name="test", version="0", phase="v1"), logging=LoggingSettings(),
        paths=PathsSettings(data_raw_dir=tmp_path / "raw", data_processed_dir=tmp_path / "processed",
                            indexes_dir=tmp_path / "indexes"),
        retrieval=RetrievalSettings(**retrieval),
    )


def write_config(tmp_path: Path) -> Path:
    config = yaml.safe_load((REPO_ROOT / "configs" / "v1.yaml").read_text())
    config["paths"] = {"data_raw_dir": str(tmp_path / "raw"), "data_processed_dir": str(tmp_path / "processed"),
                       "indexes_dir": str(tmp_path / "indexes")}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder (stand-in for BGE in tests).
    Records every embed_documents batch size so tests can check cache reuse."""

    def __init__(self, dimension: int = 64, model_name: str = "test/hashing-64"):
        self.model_name = model_name
        self._dimension = dimension
        self.document_batches: list[int] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> np.ndarray:
        v = np.zeros(self._dimension, dtype=np.float32)
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(token.encode()).hexdigest(), 16) % self._dimension] += 1.0
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    def embed_documents(self, texts):
        self.document_batches.append(len(texts))
        return np.stack([self._vector(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)
