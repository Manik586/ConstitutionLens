"""Phase 7 CLI: --corpus-id on all four scripts, run in-process with the test embedder."""
import runpy

import pytest

from constitutional_evidence_rag.corpora.models import CorpusStatus
from constitutional_evidence_rag.corpora.registry import CorpusRegistry

from corpora_fixtures import A_PAGES, B_PAGES, REPO_ROOT, embedder_factory, pdf_bytes, write_config
from llm_fakes import ScriptedLLM, reply


def script(name):
    return runpy.run_path(str(REPO_ROOT / "scripts" / f"{name}.py"))["main"]


ingest, chunk, build, query = (script(n) for n in ("ingest_corpus", "chunk_corpus", "build_indexes", "query"))


@pytest.fixture
def config(tmp_path):
    return write_config(tmp_path)


@pytest.fixture
def pdf_dir(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    (d / "document_A.pdf").write_bytes(pdf_bytes(A_PAGES))
    (d / "notes.txt").write_text("ignored: not a .pdf")
    return d


def full_build(config, pdf_dir, corpus_id="my-research"):
    assert ingest(["--corpus-id", corpus_id, "--input-dir", str(pdf_dir), "--config", str(config)]) == 0
    assert chunk(["--corpus-id", corpus_id, "--config", str(config)]) == 0
    assert build(["--corpus-id", corpus_id, "--config", str(config)], embedder_factory=embedder_factory) == 0


def test_user_corpus_end_to_end(config, pdf_dir, tmp_path, capsys):
    full_build(config, pdf_dir)
    out = capsys.readouterr().out
    assert "accepted  document_A.pdf" in out and "corpus 'my-research' is READY" in out
    assert CorpusRegistry(_paths(tmp_path)).get("my-research").status is CorpusStatus.READY

    assert query(["zeppelin hangar regulations", "--corpus-id", "my-research", "--mode", "hybrid", "--top-k", "5",
                  "--config", str(config)], embedder_factory=embedder_factory) == 0
    out = capsys.readouterr().out
    assert out.startswith(" 1. [hybrid=") and "document_A.pdf" in out

    fake = ScriptedLLM(reply(direct=[("Zeppelin hangar regulations require an inspection of every airship every thirty "
                                      "days [E1].", ["E1"], "explicit")]))
    assert query(["What do the zeppelin hangar regulations require?", "--corpus-id", "my-research", "--mode", "hybrid",
                  "--top-k", "5", "--generate", "--config", str(config)],
                 embedder_factory=embedder_factory, llm_client_factory=lambda s: fake) == 0
    out = capsys.readouterr().out
    assert "[E1] document_A.pdf — Pages 1–2" in out and "uploaded documents only" in out and "Evidence-support check" in out


def test_default_corpus_is_the_curated_one(config, curated, capsys):
    assert query(["What is Article 21?", "--config", str(config)], embedder_factory=embedder_factory) == 0
    assert capsys.readouterr().out.startswith(" 1. [hybrid=")


@pytest.mark.parametrize("corpus_id,message", [("Bad/Id", "Invalid corpus id"), ("nope", "Corpus 'nope' does not exist.")])
def test_invalid_or_missing_corpus_gives_one_clean_line(config, capsys, corpus_id, message):
    assert query(["anything", "--corpus-id", corpus_id, "--config", str(config)], embedder_factory=embedder_factory) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and message in err and "Traceback" not in err and err.count("\n") == 1


@pytest.mark.parametrize("status,message", [(CorpusStatus.UPLOADED, "has not been built yet"),
                                            (CorpusStatus.PROCESSING, "is still processing."),
                                            (CorpusStatus.FAILED, "failed to build: disk full")])
def test_not_ready_corpus_gives_one_clean_line(config, tmp_path, capsys, status, message):
    reg = CorpusRegistry(_paths(tmp_path))
    reg.create("pending")
    reg.update("pending", status=status, error="disk full" if status is CorpusStatus.FAILED else None)
    assert query(["anything", "--corpus-id", "pending", "--generate", "--config", str(config)],
                 embedder_factory=embedder_factory) == 2
    err = capsys.readouterr().err
    assert message in err and "Traceback" not in err


def test_verbose_shows_the_traceback(config, capsys):
    assert query(["anything", "--corpus-id", "nope", "--verbose", "--config", str(config)], embedder_factory=embedder_factory) == 2
    assert "Traceback" in capsys.readouterr().err


def test_ingest_guards(config, pdf_dir, capsys):
    assert ingest(["--corpus-id", "constitutional-core", "--input-dir", str(pdf_dir), "--config", str(config)]) == 2
    assert "reserved" in capsys.readouterr().err
    assert ingest(["--corpus-id", "my-research", "--config", str(config)]) == 2
    assert "--input-dir" in capsys.readouterr().err
    assert build(["--corpus-id", "my-research", "--only", "bm25", "--config", str(config)], embedder_factory=embedder_factory) == 2


def test_ingest_reports_rejected_files_and_continues(config, tmp_path, capsys):
    d = tmp_path / "mixed"
    d.mkdir()
    (d / "good.pdf").write_bytes(pdf_bytes(B_PAGES))
    (d / "fake.pdf").write_bytes(b"not really a pdf")
    assert ingest(["--corpus-id", "mixed", "--input-dir", str(d), "--config", str(config)]) == 1  # some rejected
    out = capsys.readouterr().out
    assert "rejected  fake.pdf" in out and "accepted  good.pdf" in out and "1 ingested" in out


def _paths(tmp_path):
    from constitutional_evidence_rag.common.config import PathsSettings
    return PathsSettings(data_raw_dir=tmp_path / "raw", data_processed_dir=tmp_path / "processed",
                         indexes_dir=tmp_path / "indexes", uploads_dir=tmp_path / "uploads")
