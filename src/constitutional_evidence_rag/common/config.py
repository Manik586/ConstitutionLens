"""Configuration loading for Constitutional Evidence RAG (Phase 1).

Loads configs/v1.yaml and applies environment-variable overrides for the
handful of settings .env.example exposes (LOG_LEVEL, DATA_RAW_DIR,
DATA_PROCESSED_DIR). See docs/DECISIONS.md D2 for why this only covers
the settings of phases built so far: `chunking` arrived with Phase 2;
retrieval/embedding/LLM sections are added in the phases that introduce
them, not speculatively here.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_CONFIG_PATH = Path("configs/v1.yaml")


class ConfigError(Exception):
    """Raised when configuration cannot be found, parsed, or validated."""


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    phase: str


class LoggingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    format: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


class PathsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_raw_dir: Path
    data_processed_dir: Path


class ChunkingSettings(BaseModel):
    """Chunk sizing (SRS FR-03: target 400-600 tokens, 50-100 token overlap,
    configurable). Tokens are counted by chunking.legal_chunker.count_tokens
    (D16). Defaults are the SRS midpoints."""

    model_config = ConfigDict(extra="forbid")

    target_tokens: int = Field(default=500, ge=50)
    max_tokens: int = Field(default=600, ge=50)
    overlap_tokens: int = Field(default=75, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> ChunkingSettings:
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens must be <= max_tokens")
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be < target_tokens")
        return self


class Settings(BaseModel):
    """Top-level, validated configuration for the current phase."""

    model_config = ConfigDict(extra="forbid")

    app: AppSettings
    logging: LoggingSettings
    paths: PathsSettings
    chunking: ChunkingSettings = ChunkingSettings()


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply the .env.example-documented overrides on top of YAML values.

    Only the variables .env.example actually declares are handled here —
    this function is not a generic "any env var can override any config
    key" mechanism, since that would be exactly the kind of speculative
    abstraction the project is deliberately avoiding.
    """
    raw.setdefault("logging", {})
    if "LOG_LEVEL" in os.environ:
        raw["logging"]["level"] = os.environ["LOG_LEVEL"]

    raw.setdefault("paths", {})
    if "DATA_RAW_DIR" in os.environ:
        raw["paths"]["data_raw_dir"] = os.environ["DATA_RAW_DIR"]
    if "DATA_PROCESSED_DIR" in os.environ:
        raw["paths"]["data_processed_dir"] = os.environ["DATA_PROCESSED_DIR"]

    return raw


def load_settings(config_path: Path | None = None) -> Settings:
    """Load and validate settings from a YAML config file.

    Precedence: environment variables (from .env or the process
    environment) override values from the YAML file, for the fields
    .env.example documents. Raises ConfigError if the file is missing
    or fails validation.
    """
    load_dotenv()

    path = config_path or Path(os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw = _apply_env_overrides(raw)

    try:
        return Settings(**raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {path}: {exc}") from exc
