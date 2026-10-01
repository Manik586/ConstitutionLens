"""Phase 1 -> Phase 2 end to end, through real (generated) PDFs."""
import html
import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest
import yaml

from constitutional_evidence_rag.chunking.legal_chunker import CHUNKS_FILENAME, ChunkingError, chunk_corpus, load_chunks
from constitutional_evidence_rag.common.config import ChunkingSettings
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, SourceType
from constitutional_evidence_rag.ingestion.metadata import DocumentSpec
from constitutional_evidence_rag.ingestion.pipeline import corpus_dir, ingest_corpus, ingest_document

from chunk_fixtures import CONSTITUTION, SCR_JUDGMENT

REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS = ChunkingSettings()


def write_pdf(path: Path, pages: list[str]) -> Path:
    # insert_htmlbox uses PyMuPDF's built-in Unicode fonts; base-14 Helvetica would turn "—" into "?"
    doc = pymupdf.open()
    for text in pages:
        body = "<br/>".join(html.escape(line) for line in text.splitlines())
        doc.new_page().insert_htmlbox(pymupdf.Rect(30, 30, 580, 810), f'<p style="font-size:8px">{body}</p>')
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


@pytest.fixture
def ingested(tmp_path):
    raw, processed = tmp_path / "raw", tmp_path / "processed"
    write_pdf(raw / "constitution/coi.pdf", CONSTITUTION)
    write_pdf(raw / "judgments/example.pdf", SCR_JUDGMENT)
    (raw / "manifest.yaml").write_text(yaml.safe_dump({"documents": [
        {"file": "constitution/coi.pdf", "source_type": "constitutional_text", "source_date": "2026-01-01",
         "title": "The Constitution of India (as on 2026-01-01)", "source_url": "https://example.org/coi"},
        {"file": "judgments/example.pdf", "source_type": "judgment", "source_date": "1997-08-13",
         "title": "Example v. State of Example", "source_url": "https://example.org/j/1"},
    ]}))
    ingest_corpus(raw / "manifest.yaml", raw, processed)
    return {"raw": raw, "processed": processed}


def test_pdf_to_chunks_end_to_end(ingested):
    report = chunk_corpus(ingested["processed"], CURATED_CORPUS_ID, SETTINGS)
    chunks = load_chunks(corpus_dir(ingested["processed"], CURATED_CORPUS_ID) / CHUNKS_FILENAME)

    assert report.documents == 2 and report.chunks == len(chunks) > 0
    constitution = [c for c in chunks if c.source_type is SourceType.CONSTITUTIONAL_TEXT]
    judgment = [c for c in chunks if c.source_type is SourceType.JUDGMENT]
    assert [c.article_number for c in constitution if c.article_number] == ["1", "2", "12", "14", "19", "21", "21A", "31", "52"]
    assert {c.opinion_author for c in judgment} == {None, "VERMA, CJI.", "AHMADI, CJ."}
    assert all(c.corpus_id == CURATED_CORPUS_ID for c in chunks)
    assert all(c.case_name == "Example v. State of Example" for c in judgment)


def test_rechunking_is_deterministic(ingested):
    path = corpus_dir(ingested["processed"], CURATED_CORPUS_ID) / CHUNKS_FILENAME
    chunk_corpus(ingested["processed"], CURATED_CORPUS_ID, SETTINGS)
    first = path.read_bytes()
    chunk_corpus(ingested["processed"], CURATED_CORPUS_ID, SETTINGS)

    assert path.read_bytes() == first


def test_only_the_current_version_of_each_document_is_chunked(ingested):
    write_pdf(ingested["raw"] / "judgments/example.pdf", SCR_JUDGMENT[:2])  # revised PDF -> version 2
    ingest_corpus(ingested["raw"] / "manifest.yaml", ingested["raw"], ingested["processed"])

    chunk_corpus(ingested["processed"], CURATED_CORPUS_ID, SETTINGS)
    chunks = load_chunks(corpus_dir(ingested["processed"], CURATED_CORPUS_ID) / CHUNKS_FILENAME)

    judgment_versions = {c.document_version for c in chunks if c.source_type is SourceType.JUDGMENT}
    assert judgment_versions == {2}
    assert all(c.page_end <= 2 for c in chunks if c.source_type is SourceType.JUDGMENT)


def test_chunking_one_corpus_never_touches_another(ingested, tmp_path):
    curated = corpus_dir(ingested["processed"], CURATED_CORPUS_ID)
    chunk_corpus(ingested["processed"], CURATED_CORPUS_ID, SETTINGS)
    before = {p.name: p.read_bytes() for p in curated.iterdir()}

    spec = DocumentSpec(source_type=SourceType.JUDGMENT, title="Other", source_url="https://example.org/o",
                        source_date="2000-01-01")
    ingest_document(write_pdf(tmp_path / "other.pdf", SCR_JUDGMENT), spec, corpus_id="test-collection",
                    processed_root=ingested["processed"])
    chunk_corpus(ingested["processed"], "test-collection", SETTINGS)

    assert {p.name: p.read_bytes() for p in curated.iterdir()} == before
    other = load_chunks(corpus_dir(ingested["processed"], "test-collection") / CHUNKS_FILENAME)
    assert other and all(c.corpus_id == "test-collection" for c in other)


def test_missing_ingestion_output_is_an_error(tmp_path):
    with pytest.raises(ChunkingError, match="run ingestion first"):
        chunk_corpus(tmp_path / "processed", CURATED_CORPUS_ID, SETTINGS)


# ------------------------------------------------------------------ CLI


def _run_script(tmp_path, processed: Path):
    config = yaml.safe_load((REPO_ROOT / "configs" / "v1.yaml").read_text())
    config["paths"] = {"data_raw_dir": str(tmp_path / "raw"), "data_processed_dir": str(processed)}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    env = {**os.environ, "LOG_LEVEL": "INFO", "DATA_RAW_DIR": str(tmp_path / "raw"), "DATA_PROCESSED_DIR": str(processed)}
    return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "chunk_corpus.py"), "--config", str(path)],
                          capture_output=True, text=True, cwd=REPO_ROOT, env=env)


def test_chunk_script_writes_chunks(ingested, tmp_path):
    result = _run_script(tmp_path, ingested["processed"])

    assert result.returncode == 0, result.stderr
    assert "2 documents" in result.stdout and "constitutional-core" in result.stdout
    assert (corpus_dir(ingested["processed"], CURATED_CORPUS_ID) / CHUNKS_FILENAME).exists()


def test_chunk_script_exit_code_2_without_ingestion(tmp_path):
    result = _run_script(tmp_path, tmp_path / "processed")

    assert result.returncode == 2
    assert "run ingestion first" in result.stdout + result.stderr
