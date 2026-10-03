from pathlib import Path

import pytest

from constitutional_evidence_rag.common.config import ConfigError, load_settings


def test_load_settings_from_repo_config():
    settings = load_settings(Path("configs/v1.yaml"))

    assert settings.app.name == "constitutional-evidence-rag"
    assert settings.app.phase == "v1"
    assert settings.logging.level == "INFO"
    assert settings.paths.data_raw_dir == Path("data/raw")
    assert settings.paths.data_processed_dir == Path("data/processed")


def test_missing_config_file_raises_config_error(tmp_path):
    missing = tmp_path / "does-not-exist.yaml"

    with pytest.raises(ConfigError):
        load_settings(missing)


def test_env_var_overrides_log_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = load_settings(Path("configs/v1.yaml"))

    assert settings.logging.level == "DEBUG"


def test_env_var_overrides_data_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_RAW_DIR", str(tmp_path / "raw"))

    settings = load_settings(Path("configs/v1.yaml"))

    assert settings.paths.data_raw_dir == tmp_path / "raw"


def test_incomplete_yaml_raises_config_error(tmp_path):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("app:\n  name: test\n")  # missing version/phase/logging/paths

    with pytest.raises(ConfigError):
        load_settings(bad_config)


def test_unknown_key_raises_config_error(tmp_path):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text(
        "app:\n"
        "  name: test\n"
        "  version: '0.1.0'\n"
        "  phase: v1\n"
        "  unexpected_key: oops\n"
        "logging:\n"
        "  level: INFO\n"
        "paths:\n"
        "  data_raw_dir: data/raw\n"
        "  data_processed_dir: data/processed\n"
    )

    with pytest.raises(ConfigError):
        load_settings(bad_config)


def test_chunking_settings_from_repo_config():
    settings = load_settings(Path("configs/v1.yaml"))

    assert (settings.chunking.target_tokens, settings.chunking.max_tokens, settings.chunking.overlap_tokens) == (500, 600, 75)


@pytest.mark.parametrize("chunking", ["{target_tokens: 700, max_tokens: 600}", "{target_tokens: 400, overlap_tokens: 400}"])
def test_inconsistent_chunking_settings_raise_config_error(tmp_path, chunking):
    config = tmp_path / "c.yaml"
    config.write_text(
        "app: {name: t, version: '0', phase: v1}\n"
        "logging: {level: INFO}\n"
        "paths: {data_raw_dir: r, data_processed_dir: p}\n"
        f"chunking: {chunking}\n"
    )

    with pytest.raises(ConfigError):
        load_settings(config)


def test_phase3_settings_from_repo_config():
    settings = load_settings(Path("configs/v1.yaml"))

    assert settings.paths.indexes_dir == Path("indexes")
    assert settings.embedding.model_name == "BAAI/bge-small-en-v1.5"
    r = settings.retrieval
    assert (r.top_n_bm25, r.top_n_dense, r.top_n_hybrid, r.rrf_k) == (20, 20, 20, 60)


def test_env_var_overrides_indexes_dir(monkeypatch):
    monkeypatch.setenv("INDEXES_DIR", "/tmp/somewhere")

    assert load_settings(Path("configs/v1.yaml")).paths.indexes_dir == Path("/tmp/somewhere")


@pytest.mark.parametrize("retrieval", ["{weight_bm25: 0, weight_dense: 0}", "{bm25_b: 1.5}", "{top_n_hybrid: 0}", "{rrf_k: 0}"])
def test_invalid_retrieval_settings_raise_config_error(tmp_path, retrieval):
    config = tmp_path / "c.yaml"
    config.write_text(
        "app: {name: t, version: '0', phase: v1}\n"
        "logging: {level: INFO}\n"
        "paths: {data_raw_dir: r, data_processed_dir: p}\n"
        f"retrieval: {retrieval}\n"
    )

    with pytest.raises(ConfigError):
        load_settings(config)


def test_phase4_settings_from_repo_config():
    settings = load_settings(Path("configs/v1.yaml"))
    e, g = settings.evidence, settings.generation

    assert (e.top_k, e.candidate_pool, e.min_term_coverage, e.min_dense_score) == (6, 50, 0.6, None)
    assert (g.backend, g.show_opinion_author) == ("extractive", False)


@pytest.mark.parametrize("section", ["evidence: {min_term_coverage: 1.5}", "evidence: {top_k: 0}",
                                     "generation: {backend: gpt}", "evidence: {provision_match_boost: -1}"])
def test_invalid_phase4_settings_raise_config_error(tmp_path, section):
    config = tmp_path / "c.yaml"
    config.write_text("app: {name: t, version: '0', phase: v1}\nlogging: {level: INFO}\n"
                      f"paths: {{data_raw_dir: r, data_processed_dir: p}}\n{section}\n")
    with pytest.raises(ConfigError):
        load_settings(config)
