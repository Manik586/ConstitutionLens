"""Configuration loading for Constitutional Evidence RAG (Phase 1).

Loads configs/v1.yaml and applies environment-variable overrides for the
handful of settings .env.example exposes (LOG_LEVEL, DATA_RAW_DIR,
DATA_PROCESSED_DIR, INDEXES_DIR). See docs/DECISIONS.md D2 for why this only
covers the settings of phases built so far: `chunking` arrived with Phase 2,
`embedding` and `retrieval` with Phase 3; reranking/LLM sections are added in
the phases that introduce them, not speculatively here.
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
    indexes_dir: Path = Path("indexes")  # Phase 3; per-corpus subdirectories (D15, FR-05a)


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


class EmbeddingSettings(BaseModel):
    """Dense embedding model (SRS FR-05, §39 "BGE or comparable"; D17).

    `query_instruction` is prepended to queries only (not to chunks), as the
    BGE v1.5 model card recommends for short-query retrieval.
    """

    model_config = ConfigDict(extra="forbid")

    model_name: str = "BAAI/bge-small-en-v1.5"
    query_instruction: str = "Represent this sentence for searching relevant passages: "
    batch_size: int = Field(default=32, ge=1)
    device: str = "cpu"


class RetrievalSettings(BaseModel):
    """BM25, dense and hybrid retrieval (SRS FR-05 - FR-07; D18, D19)."""

    model_config = ConfigDict(extra="forbid")

    bm25_k1: float = Field(default=1.5, gt=0)
    bm25_b: float = Field(default=0.75, ge=0, le=1)
    top_n_bm25: int = Field(default=20, ge=1)  # FR-06 default 20
    top_n_dense: int = Field(default=20, ge=1)  # FR-05 default 20
    top_n_hybrid: int = Field(default=20, ge=1)  # candidates handed to Phase 4 reranking (FR-08: 20 -> 5)
    rrf_k: int = Field(default=60, ge=1)  # FR-07 fixed RRF constant
    weight_bm25: float = Field(default=1.0, ge=0)
    weight_dense: float = Field(default=1.0, ge=0)

    @model_validator(mode="after")
    def _weights(self) -> RetrievalSettings:
        if self.weight_bm25 == 0 and self.weight_dense == 0:
            raise ValueError("at least one of weight_bm25 / weight_dense must be > 0")
        return self


class Settings(BaseModel):
    """Top-level, validated configuration for the current phase."""

    model_config = ConfigDict(extra="forbid")

    app: AppSettings
    logging: LoggingSettings
    paths: PathsSettings
    chunking: ChunkingSettings = ChunkingSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    retrieval: RetrievalSettings = RetrievalSettings()


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
    if "INDEXES_DIR" in os.environ:
        raw["paths"]["indexes_dir"] = os.environ["INDEXES_DIR"]

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
