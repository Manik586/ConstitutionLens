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
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, CorpusId

DEFAULT_CONFIG_PATH = Path("configs/v1.yaml")
REPO_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "v1.yaml"  # src/<pkg>/common/config.py -> repo


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
    uploads_dir: Path = Path("data/uploads")  # Phase 7: original uploaded PDFs, data/uploads/<corpus_id>/
    models_dir: Path | None = None  # Phase 7: model cache (sets HF_HOME for the service if not already set)
    # Phase 7: when set (CER_DATA_ROOT), every relative path above resolves against it instead of
    # the process working directory. Unset keeps the historical CWD-relative behaviour.
    root: Path | None = None

    @model_validator(mode="after")
    def _resolve_against_root(self) -> PathsSettings:
        if self.root is not None:
            for name in ("data_raw_dir", "data_processed_dir", "indexes_dir", "uploads_dir", "models_dir"):
                value = getattr(self, name)
                if value is not None and not value.is_absolute():
                    object.__setattr__(self, name, self.root / value)
        return self


class CorpusSettings(BaseModel):
    """Phase 7: which corpus scripts and the service use when none is named (D27)."""

    model_config = ConfigDict(extra="forbid")

    default_corpus_id: CorpusId = CURATED_CORPUS_ID


class ApiSettings(BaseModel):
    """HTTP API exposure (D33). Defaults keep the Phase 7/8 behaviour: same-origin only, no token.

    cors_allowed_origins: exact origins (e.g. "https://my-app.vercel.app") whose pages may call
    the API from the browser, for a frontend hosted elsewhere. Never "*".
    access_token: optional shared token. When set, every API route except /health requires the
    header X-Access-Token. Set it in the environment (API_ACCESS_TOKEN), never in YAML."""

    model_config = ConfigDict(extra="forbid")

    cors_allowed_origins: list[str] = Field(default_factory=list)
    access_token: str | None = Field(default=None, min_length=16, repr=False)

    @field_validator("cors_allowed_origins")
    @classmethod
    def _exact_origins(cls, origins: list[str]) -> list[str]:
        cleaned = []
        for origin in origins:
            origin = origin.strip().rstrip("/")
            if not origin:
                continue
            if origin == "*" or not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?", origin):
                raise ValueError(f"CORS origin must be an exact scheme://host[:port], got {origin!r}")
            cleaned.append(origin)
        return cleaned


class UploadSettings(BaseModel):
    """Phase 7: limits for user-uploaded PDFs (D27). Enforced per file; one bad file never
    aborts the others."""

    model_config = ConfigDict(extra="forbid")

    max_file_size_mb: float = Field(default=20, gt=0)
    max_documents_per_corpus: int = Field(default=20, ge=1)
    max_total_size_mb: float = Field(default=200, gt=0)
    max_pages_per_document: int = Field(default=500, ge=1)
    allowed_extensions: list[str] = Field(default_factory=lambda: [".pdf"], min_length=1)


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


class RerankingSettings(BaseModel):
    """SRS FR-08 cross-encoder reranking (Experiment C). Off by default: when enabled, the
    hybrid candidate pool is re-scored by a cross-encoder that reads the question and each
    passage together. The score is query relevance only (no source-type weight, Invariant 1)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    model_name: str = "BAAI/bge-reranker-base"
    candidates: int = Field(default=30, ge=1)  # hybrid pool size handed to the cross-encoder
    max_length: int = Field(default=512, ge=32)  # tokens of question + passage the model reads
    batch_size: int = Field(default=16, ge=1)
    device: str = "cpu"


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
    reranking: RerankingSettings = RerankingSettings()
    evidence: EvidenceSettings = EvidenceSettings()
    generation: GenerationSettings = GenerationSettings()
    llm: LLMSettings = LLMSettings()
    evidence_validation: EvidenceValidationSettings = EvidenceValidationSettings()
    corpus: CorpusSettings = CorpusSettings()
    uploads: UploadSettings = UploadSettings()
    api: ApiSettings = ApiSettings()


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
    # Deployment-specific consent switches: set per machine in .env so the committed YAML keeps
    # the safe defaults (allow_remote: false). Pydantic validates the value ("true"/"false"/...).
    for env, key in (("LLM_ALLOW_REMOTE", "allow_remote"), ("LLM_REQUIRE_API_KEY", "require_api_key")):
        if os.environ.get(env):
            raw.setdefault("llm", {})[key] = os.environ[env].strip()
    # Phase 7 (D27)
    for env, key in (("CER_DATA_ROOT", "root"), ("UPLOADS_DIR", "uploads_dir"), ("MODELS_DIR", "models_dir")):
        if os.environ.get(env):
            raw["paths"][key] = os.environ[env]
    if os.environ.get("DEFAULT_CORPUS_ID"):
        raw.setdefault("corpus", {})["default_corpus_id"] = os.environ["DEFAULT_CORPUS_ID"]
    for env, key in (("UPLOAD_MAX_FILE_SIZE_MB", "max_file_size_mb"), ("UPLOAD_MAX_DOCUMENTS", "max_documents_per_corpus"),
                     ("UPLOAD_MAX_TOTAL_SIZE_MB", "max_total_size_mb"), ("UPLOAD_MAX_PAGES", "max_pages_per_document")):
        if os.environ.get(env):
            raw.setdefault("uploads", {})[key] = os.environ[env]
    # D33: a frontend on another origin, and the optional shared access token (env only).
    if os.environ.get("CORS_ALLOWED_ORIGINS"):
        raw.setdefault("api", {})["cors_allowed_origins"] = os.environ["CORS_ALLOWED_ORIGINS"].split(",")
    if os.environ.get("API_ACCESS_TOKEN"):
        raw.setdefault("api", {})["access_token"] = os.environ["API_ACCESS_TOKEN"].strip()

    return raw


def load_settings(config_path: Path | None = None) -> Settings:
    """Load and validate settings from a YAML config file.

    Precedence: environment variables (from .env or the process
    environment) override values from the YAML file, for the fields
    .env.example documents. Raises ConfigError if the file is missing
    or fails validation.
    """
    load_dotenv()

    path = config_path or Path(os.environ.get("CER_CONFIG") or os.environ.get("CONFIG_PATH") or DEFAULT_CONFIG_PATH)
    if config_path is None and path == DEFAULT_CONFIG_PATH and not path.exists() and REPO_CONFIG_PATH.exists():
        path = REPO_CONFIG_PATH  # Phase 7: work from any directory, not only the repo root
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw = _apply_env_overrides(raw)

    try:
        return Settings(**raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {path}: {exc}") from exc
