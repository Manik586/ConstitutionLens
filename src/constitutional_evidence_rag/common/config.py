"""Configuration loading for Constitutional Evidence RAG (Phase 1).

Loads configs/v1.yaml and applies environment-variable overrides for the
handful of settings .env.example exposes (LOG_LEVEL, DATA_RAW_DIR,
DATA_PROCESSED_DIR, INDEXES_DIR, LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL; the LLM API key
is read separately, from the variable named by llm.api_key_env). See docs/DECISIONS.md D2 for why this only
covers the settings of phases built so far: `chunking` arrived with Phase 2,
`embedding` and `retrieval` with Phase 3; reranking/LLM sections are added in
the phases that introduce them, not speculatively here.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

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


class EvidenceSettings(BaseModel):
    """Evidence selection above retrieval (Phase 4 as scoped in D20).

    Boosts are in RRF units (one rank-1 hit = 1/(rrf_k + 1) ~ 0.016). They are
    *locator* matches (this chunk is the requested Article / belongs to the named
    case / mentions the requested Article) — never source-type or authority
    weights (SRS §20.2, FR-SA-05).
    """

    model_config = ConfigDict(extra="forbid")

    candidate_pool: int = Field(default=50, ge=1)  # BM25 and dense top-n fused for answering
    top_k: int = Field(default=6, ge=1)  # evidence items handed to the generator
    max_per_document: int = Field(default=2, ge=1)  # diversity cap (not applied to a named case)
    max_constitutional: int = Field(default=2, ge=0)
    provision_match_boost: float = Field(default=1.0, ge=0)
    case_match_boost: float = Field(default=1.0, ge=0)
    article_mention_boost: float = Field(default=0.01, ge=0)
    min_term_coverage: float = Field(default=0.6, ge=0, le=1)  # best single-item share of the query's content terms
    min_dense_score: float | None = Field(default=None, ge=-1, le=1)  # optional cosine floor; calibrate per model


class GenerationSettings(BaseModel):
    """Answer generation (SRS FR-10 - FR-12, FR-14; D21). Only the deterministic
    extractive backend exists until an LLM provider is chosen (open decision)."""

    model_config = ConfigDict(extra="forbid")

    backend: Literal["extractive", "llm"] = "extractive"  # "llm" = Phase 5 grounded LLM generation (D23-D25)
    sentences_per_item: int = Field(default=2, ge=1)
    max_quote_chars: int = Field(default=450, ge=80)
    # Name the judge whose opinion a passage comes from. Off by default: Phase 2 author
    # detection misses some opinion openings ("BHAGWATI, J.-The ...", "DR. D.Y. CHANDRACHUD,
    # J."), so the previous author's name would be shown (D21). Enable once that is fixed.
    show_opinion_author: bool = False
    # Phase 5 (LLM backend): what goes into the prompt context.
    prompt_version: Literal["grounded-v1"] = "grounded-v1"
    context_max_items: int = Field(default=6, ge=1)  # evidence items sent to the LLM (in Phase 4 order)
    context_max_chars_per_item: int = Field(default=3000, ge=200)  # longer chunk text is truncated, visibly
    context_max_total_chars: int = Field(default=18000, ge=1000)  # items beyond this budget are omitted, visibly


class LLMSettings(BaseModel):
    """LLM provider for Phase 5 generation (D23). Off by default: nothing is sent anywhere
    until a provider is configured. The API key is read from the environment variable
    named by api_key_env — the key itself never appears in configuration."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["none", "openai_compatible"] = "none"
    model: str | None = None
    base_url: str | None = None  # e.g. https://api.example.com/v1 or http://localhost:11434/v1
    api_key_env: str = "LLM_API_KEY"
    require_api_key: bool = True  # set false for local servers that need no key
    allow_remote: bool = False  # corpus evidence is sent to a non-localhost base_url only if true
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_output_tokens: int = Field(default=800, ge=16)
    timeout_seconds: float = Field(default=60.0, gt=0)
    json_mode: bool = False  # request response_format=json_object (only if the server supports it)
    max_retries: int = Field(default=1, ge=0, le=3)  # re-asks after a malformed (non-JSON / off-schema) reply


class EvidenceValidationSettings(BaseModel):
    """Phase 6 claim-level evidence-support check (SRS FR-13 Basic Evidence Check; D26).

    Heuristic thresholds, conservative by design: when in doubt a claim is
    "insufficient", never "supported". The semantic method reuses the project's
    embedding model; its thresholds are uncalibrated until measured on BGE scores.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True  # applies to LLM answers (generation.backend: llm / --generate)
    method: Literal["lexical", "semantic"] = "lexical"
    support_coverage: float = Field(default=0.75, gt=0, le=1)  # share of the claim's key terms found in its evidence
    min_claim_terms: int = Field(default=2, ge=1)  # fewer key terms than this -> too little content to check
    contribution_min: float = Field(default=0.3, ge=0, le=1)  # a cited item covering less than this is dropped as a citation
    contradiction_min_overlap: float = Field(default=0.5, gt=0, le=1)  # shared subject needed before a pattern counts as contradiction
    semantic_floor: float = Field(default=0.5, ge=-1, le=1)  # semantic: below this cosine, never "supported"
    semantic_support_threshold: float = Field(default=0.8, ge=-1, le=1)  # semantic: paraphrase path needs this cosine ...
    semantic_support_coverage: float = Field(default=0.5, gt=0, le=1)  # ... and at least this coverage


class Settings(BaseModel):
    """Top-level, validated configuration for the current phase."""

    model_config = ConfigDict(extra="forbid")

    app: AppSettings
    logging: LoggingSettings
    paths: PathsSettings
    chunking: ChunkingSettings = ChunkingSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    retrieval: RetrievalSettings = RetrievalSettings()
    evidence: EvidenceSettings = EvidenceSettings()
    generation: GenerationSettings = GenerationSettings()
    llm: LLMSettings = LLMSettings()
    evidence_validation: EvidenceValidationSettings = EvidenceValidationSettings()


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
    for env, key in (("LLM_PROVIDER", "provider"), ("LLM_MODEL", "model"), ("LLM_BASE_URL", "base_url")):
        if os.environ.get(env):
            raw.setdefault("llm", {})[key] = os.environ[env]

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
