"""Phase 4 fixture corpus: constitutional Articles 14, 21, 32 and three judgments
that cite those Articles more often than the provisions do (so locator matching,
not luck, must put each provision first), plus a document registry. Synthetic
text, not quotations from real judgments."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from constitutional_evidence_rag.chunking.legal_chunker import CHUNKS_FILENAME, count_tokens
from constitutional_evidence_rag.common.chunks import Chunk, ChunkingMethod, Division
from constitutional_evidence_rag.common.config import AppSettings, LoggingSettings, PathsSettings, Settings
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, DocumentMetadata, SourceType
from constitutional_evidence_rag.ingestion.metadata import write_registry
from constitutional_evidence_rag.ingestion.pipeline import METADATA_FILENAME, corpus_dir

REPO_ROOT = Path(__file__).resolve().parents[3]
CONST, MANEKA, KESAVA, WRIT = "CONST-00000000c0c0", "JUDG-000000000a01", "JUDG-000000000a02", "JUDG-000000000a03"
TITLES = {
    CONST: ("The Constitution of India (as on 1st May, 2026)", SourceType.CONSTITUTIONAL_TEXT, date(2026, 5, 1)),
    MANEKA: ("Maneka Gandhi v. Union of India", SourceType.JUDGMENT, date(1978, 1, 25)),
    KESAVA: ("His Holiness Kesavananda Bharati Sripadagalvaru v. State of Kerala", SourceType.JUDGMENT, date(1973, 4, 24)),
    WRIT: ("Example Writ Petitioners v. State of Example", SourceType.JUDGMENT, date(1990, 3, 1)),
}
# (document, text, metadata)
CORPUS = [
    (CONST, "14. Equality before law.—The State shall not deny to any person equality before the law or the equal "
            "protection of the laws within the territory of India.", {"article_number": "14", "article_title": "Equality before law"}),
    (CONST, "21. Protection of life and personal liberty.—No person shall be deprived of his life or personal liberty "
            "except according to procedure established by law.", {"article_number": "21", "article_title": "Protection of life and personal liberty"}),
    (CONST, "32. Remedies for enforcement of rights conferred by this Part.—(1) The right to move the Supreme Court by "
            "appropriate proceedings for the enforcement of the rights conferred by this Part is guaranteed.",
     {"article_number": "32", "article_title": "Remedies for enforcement of rights conferred by this Part"}),
    (MANEKA, "The procedure contemplated by Article 21 must be right and just and fair. Article 21 and Article 14 are "
             "not mutually exclusive. The Supreme Court interpreted Article 21 broadly in this judgment.",
     {"division": Division.OPINION, "opinion_author": "BHAGWATI, J."}),
    (MANEKA, "Article 14 strikes at arbitrariness in State action and ensures fairness and equality of treatment. "
             "The principle of reasonableness pervades Article 14 like a brooding omnipresence.",
     {"division": Division.OPINION, "opinion_author": "BHAGWATI, J."}),
    (KESAVA, "HELD : The amending power under Article 368 does not extend to altering the basic structure of the "
             "Constitution. The basic structure doctrine limits Parliament.", {"division": Division.FRONT_MATTER, "heading": "HELD"}),
    (KESAVA, "Counsel for the petitioners submitted that the twenty-fourth amendment was invalid. The basic structure "
             "argument was pressed at length.", {"division": Division.OPINION, "opinion_author": "SIKRI, C.J."}),
    (WRIT, "The right to move this Court under Article 32 is itself a fundamental right. Article 32 provides the "
           "remedy, and Article 32 cannot be abrogated except as the Constitution provides.",
     {"division": Division.OPINION, "opinion_author": "EXAMPLE, J."}),
    (WRIT, "The petition under Article 32 was heard with petitions alleging violation of Article 14 and Article 21.",
     {"division": Division.OPINION, "opinion_author": "EXAMPLE, J."}),
]


def make_chunks() -> list[Chunk]:
    chunks, per_doc = [], {}
    for document_id, text, extra in CORPUS:
        i = per_doc.get(document_id, 0)
        per_doc[document_id] = i + 1
        _, source_type, _ = TITLES[document_id]
        chunks.append(Chunk(
            chunk_id=f"{document_id}@v1:{i:04d}:{hashlib.sha256(text.encode()).hexdigest()[:8]}",
            corpus_id=CURATED_CORPUS_ID, document_id=document_id, document_version=1, source_type=source_type,
            source_url=f"https://example.org/{document_id}", chunk_index=i, page_start=20 + 3 * i, page_end=21 + 3 * i,
            text=text, token_count=count_tokens(text), chunking_method=ChunkingMethod.STRUCTURE,
            case_name=TITLES[document_id][0] if source_type is SourceType.JUDGMENT else None, **extra,
        ))
    return chunks


def registry_rows() -> list[DocumentMetadata]:
    return [DocumentMetadata(document_id=d, corpus_id=CURATED_CORPUS_ID, source_type=st, title=t,
                             source_url=f"https://example.org/{d}", document_version=1, source_date=dt,
                             ingestion_date=datetime(2026, 9, 1, tzinfo=timezone.utc))
            for d, (t, st, dt) in TITLES.items()]


def write_corpus(processed_root: Path, chunks: list[Chunk]) -> None:
    target = corpus_dir(processed_root, CURATED_CORPUS_ID)
    target.mkdir(parents=True, exist_ok=True)
    (target / CHUNKS_FILENAME).write_text("".join(c.model_dump_json() + "\n" for c in chunks), encoding="utf-8")
    write_registry(target / METADATA_FILENAME, registry_rows())


def make_settings(tmp_path: Path, **sections) -> Settings:
    return Settings(app=AppSettings(name="t", version="0", phase="v1"), logging=LoggingSettings(),
                    paths=PathsSettings(data_raw_dir=tmp_path / "raw", data_processed_dir=tmp_path / "processed",
                                        indexes_dir=tmp_path / "indexes"), **sections)


def write_config(tmp_path: Path) -> Path:
    config = yaml.safe_load((REPO_ROOT / "configs" / "v1.yaml").read_text())
    config["paths"] = {"data_raw_dir": str(tmp_path / "raw"), "data_processed_dir": str(tmp_path / "processed"),
                       "indexes_dir": str(tmp_path / "indexes")}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


class HashingEmbedder:
    """Deterministic stand-in for BGE (no weights downloaded)."""

    def __init__(self, dimension: int = 64, model_name: str = "test/hashing-64"):
        self.model_name, self._dimension = model_name, dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> np.ndarray:
        v = np.zeros(self._dimension, dtype=np.float32)
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(token.encode()).hexdigest(), 16) % self._dimension] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(self, texts):
        return np.stack([self._vector(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)
