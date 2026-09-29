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
