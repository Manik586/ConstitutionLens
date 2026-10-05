"""Test-wide isolation.

`load_settings()` calls `load_dotenv()`, which finds the repo's `.env`
(which docker-compose requires to exist) and writes its values into
`os.environ` for the rest of the process — so a developer's local
LOG_LEVEL=DEBUG would fail config tests that expect the YAML defaults.
Several tests also use repo-relative paths like `configs/v1.yaml`.

This fixture runs every test from the repo root with the documented
override variables unset and `.env` loading disabled. Tests that need an
override set it themselves via `monkeypatch.setenv`.
"""
from pathlib import Path

import pytest

from constitutional_evidence_rag.common import config as config_module

REPO_ROOT = Path(__file__).resolve().parents[1]
_OVERRIDE_VARS = ("LOG_LEVEL", "DATA_RAW_DIR", "DATA_PROCESSED_DIR", "INDEXES_DIR", "CONFIG_PATH",
                  "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY")


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    for var in _OVERRIDE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
