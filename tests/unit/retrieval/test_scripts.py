"""scripts/build_indexes.py and scripts/query.py, run in-process with the
deterministic test embedder (no model download)."""
import json
import runpy

import pytest

from constitutional_evidence_rag.common.retrieval import RetrievedChunk

from retrieval_fixtures import REPO_ROOT, HashingEmbedder, make_chunks, write_chunks, write_config

build_main = runpy.run_path(str(REPO_ROOT / "scripts" / "build_indexes.py"))["main"]
query_main = runpy.run_path(str(REPO_ROOT / "scripts" / "query.py"))["main"]
FAKE = lambda settings: HashingEmbedder()


@pytest.fixture
def config(tmp_path):
    write_chunks(tmp_path / "processed", make_chunks())
    return write_config(tmp_path)


def test_build_then_query(config, tmp_path, capsys):
    assert build_main(["--config", str(config)], embedder_factory=FAKE) == 0
    out = capsys.readouterr().out
    assert "bm25   8 chunks" in out and "dense  8 vectors x 64" in out and "8 embedded, 0 reused" in out
    assert (tmp_path / "indexes" / "constitutional-core" / "bm25" / "meta.json").exists()
    assert (tmp_path / "indexes" / "constitutional-core" / "dense" / "index.faiss").exists()

    assert build_main(["--config", str(config), "--only", "dense"], embedder_factory=FAKE) == 0
    assert "0 embedded, 8 reused" in capsys.readouterr().out  # embeddings not recomputed

    assert query_main(["Article 19(2)", "--config", str(config), "--top-k", "3"], embedder_factory=FAKE) == 0
    out = capsys.readouterr().out
    assert out.startswith(" 1. [hybrid=") and "Speech Case v. Union of India" in out


@pytest.mark.parametrize("mode", ["bm25", "dense", "hybrid"])
def test_query_json_output_is_valid(config, capsys, mode):
    build_main(["--config", str(config)], embedder_factory=FAKE)
    capsys.readouterr()
    assert query_main(["reasonable restrictions", "--mode", mode, "--json", "--config", str(config)], embedder_factory=FAKE) == 0
    rows = [RetrievedChunk.model_validate(json.loads(line)) for line in capsys.readouterr().out.splitlines()]
    assert rows and all(r.method.value == mode for r in rows)


def test_bm25_only_build_needs_no_model(config, capsys):
    def no_model(settings):
        raise AssertionError("embedder must not be loaded for --only bm25")

    assert build_main(["--config", str(config), "--only", "bm25"], embedder_factory=no_model) == 0
    assert query_main(["liberty", "--mode", "bm25", "--config", str(config)], embedder_factory=no_model) == 0


def test_error_exit_codes(config, tmp_path, capsys):
    def unavailable(settings):
        raise OSError("model download blocked")

    assert build_main(["--config", str(config), "--only", "dense"], embedder_factory=unavailable) == 2
    assert query_main(["liberty", "--config", str(config)], embedder_factory=FAKE) == 2  # no indexes yet
    build_main(["--config", str(config)], embedder_factory=FAKE)
    assert query_main(["   ", "--config", str(config)], embedder_factory=FAKE) == 2  # empty query
    (tmp_path / "processed" / "constitutional-core" / "chunks.jsonl").unlink()
    assert build_main(["--config", str(config)], embedder_factory=FAKE) == 2  # no chunks
