"""Smoke test for scripts/ingest_corpus.py, run as a real subprocess."""
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ingest_corpus.py"


def _config(tmp_path, raw, processed) -> Path:
    config = yaml.safe_load((REPO_ROOT / "configs" / "v1.yaml").read_text())
    config["paths"] = {"data_raw_dir": str(raw), "data_processed_dir": str(processed)}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def _run(config: Path):
    # The subprocess loads the real repo .env if one exists; setting the
    # override variables explicitly wins over it (load_dotenv never
    # overrides variables that are already set), keeping the test hermetic.
    paths = yaml.safe_load(config.read_text())["paths"]
    env = {**os.environ, "LOG_LEVEL": "INFO",
           "DATA_RAW_DIR": paths["data_raw_dir"], "DATA_PROCESSED_DIR": paths["data_processed_dir"]}
    return subprocess.run([sys.executable, str(SCRIPT), "--config", str(config)],
                          capture_output=True, text=True, cwd=REPO_ROOT, env=env)


def test_script_ingests_default_manifest(corpus, tmp_path):
    result = _run(_config(tmp_path, corpus["raw"], corpus["processed"]))

    assert result.returncode == 0, result.stderr
    assert "2 ingested, 0 unchanged, 0 failed" in result.stdout
    assert "no text on pages [2]" in result.stdout
    # V1 CLI always writes the curated corpus, in its own directory (D15)
    assert (corpus["processed"] / "constitutional-core" / "documents.jsonl").exists()
    assert (corpus["processed"] / "constitutional-core" / "metadata.jsonl").exists()
    assert "constitutional-core" in result.stdout


def test_script_exit_code_1_when_a_document_fails(corpus, tmp_path):
    (corpus["raw"] / "judgments" / "synthetic_judgment.pdf").unlink()

    result = _run(_config(tmp_path, corpus["raw"], corpus["processed"]))

    assert result.returncode == 1
    assert "1 ingested, 0 unchanged, 1 failed" in result.stdout
    assert "MissingPDFError" in result.stdout


def test_script_exit_code_2_on_missing_manifest(tmp_path):
    result = _run(_config(tmp_path, tmp_path / "raw", tmp_path / "processed"))

    assert result.returncode == 2
    assert "Manifest not found" in result.stdout + result.stderr
