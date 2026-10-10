"""Thin FastAPI app over the corpus service (Phase 7; D29). Optional extra: pip install -e ".[api]".

    uvicorn --factory constitutional_evidence_rag.api.app:create_app --host 0.0.0.0 --port 8000

    GET  /health                    liveness + non-secret build info
    POST /corpora                   {corpus_id, name?}                  -> 201 Corpus | 409 exists/reserved | 422 invalid id
    GET  /corpora                   all corpora, constitutional-core first
    GET  /corpora/{id}              one corpus (status, counts, error)  -> 404 if missing
    DELETE /corpora/{id}            delete a user corpus                -> 409 reserved/busy
    POST /corpora/{id}/documents    multipart PDFs ("files")            -> per-file accepted/unchanged/rejected
    POST /corpora/{id}/build        start a background build            -> 202, then poll GET /corpora/{id}
    POST /corpora/{id}/query        {question, mode, top_k, generate}   -> Answer JSON (citations + Phase 6 validation)
    GET  /config                    non-secret facts the UI needs (generation on/off, upload limits, query options)
    GET  /                          ConstitutionLens web UI (Phase 8; D30) -> web/static/index.html
    GET  /static/...                UI assets (CSP, nosniff and no-referrer headers on / and /static only)

No authentication: deploy only where every user is trusted (SRS §40.3 `private` profile).
Errors are always {"error": <code>, "message": <user-safe text>} — never tracebacks, server
paths or keys; the original exception is logged server-side. When `generate` is true, retrieved
user-document text is sent to the configured LLM provider (see README).
"""
from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from constitutional_evidence_rag.common.config import Settings, load_settings
from constitutional_evidence_rag.common.logging import configure_logging, get_logger
from constitutional_evidence_rag.corpora.errors import (
    CorpusBuildError, CorpusBusyError, CorpusError, CorpusExistsError, CorpusNotFoundError, CorpusNotReadyError,
    InvalidCorpusIdError, ReservedCorpusError,
)
from constitutional_evidence_rag.corpora.models import CorpusStatus
from constitutional_evidence_rag.corpora.service import CorpusService
from constitutional_evidence_rag.generation.grounding import GroundingError
from constitutional_evidence_rag.generation.llm import LLMConfigurationError
from constitutional_evidence_rag.retrieval.store import QueryError, RetrievalIndexError

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"

# The Windows registry can map .js to text/plain, which browsers refuse for ES modules.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

# Phase 8 (D30): applied to the UI page and its assets only; API responses are unchanged.
UI_SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
                                "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
                                "form-action 'self'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
QUERY_TOP_K_MAX = 20  # QueryRequest.top_k bound

_STATUS = {InvalidCorpusIdError: 422, CorpusExistsError: 409, ReservedCorpusError: 409, CorpusNotFoundError: 404,
           CorpusBusyError: 409, CorpusBuildError: 422}


class CreateCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corpus_id: str
    name: str | None = Field(default=None, max_length=120)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)  # /config reports max_question_chars
    mode: str = "hybrid"
    top_k: int | None = Field(default=None, ge=1, le=QUERY_TOP_K_MAX)
    generate: bool = False


def _error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message}, headers=headers)


def create_app(settings: Settings | None = None, service: CorpusService | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.logging.level, settings.logging.format)
    service = service or CorpusService(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.startup()
        yield
        service.shutdown()

    app = FastAPI(title="Constitutional Evidence RAG", version=settings.app.version, lifespan=lifespan)
    app.state.service = service

    @app.middleware("http")
    async def _ui_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers.update(UI_SECURITY_HEADERS)
        return response

    # ------------------------------------------------------------------ errors

    @app.exception_handler(CorpusNotReadyError)
    async def _not_ready(request: Request, exc: CorpusNotReadyError):
        if exc.status is CorpusStatus.PROCESSING:
            return _error(503, "corpus_processing", exc.message, {"Retry-After": "5"})
        return _error(409, exc.code, exc.message)

    @app.exception_handler(CorpusError)
    async def _corpus(request: Request, exc: CorpusError):
        return _error(_STATUS.get(type(exc), 400), exc.code, exc.message)

    @app.exception_handler(QueryError)
    async def _query(request: Request, exc: QueryError):
        return _error(422, "invalid_query", str(exc))

    @app.exception_handler(LLMConfigurationError)
    async def _llm(request: Request, exc: LLMConfigurationError):
        logger.warning("Generation requested but the LLM is not usable: %s", exc)
        return _error(503, "generation_unavailable", "Answer generation is not configured on this server.")

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return _error(422, "invalid_request", f"{where or 'request'}: {first.get('msg', 'invalid value')}")

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        code = "index_error" if isinstance(exc, (RetrievalIndexError, GroundingError)) else "internal_error"
        return _error(500, code, "Internal error; the details are in the server log.")

    # ------------------------------------------------------------------ routes

    @app.get("/health")
    def health():
        return {"status": "ok", "version": settings.app.version, "default_corpus_id": settings.corpus.default_corpus_id}

    @app.get("/config")
    def config():
        # Only what the UI needs to offer valid choices. No paths, URLs, model names or keys.
        llm = settings.llm
        return {
            "generation_available": service.generation_available(),
            "default_corpus_id": settings.corpus.default_corpus_id,
            "reserved_corpus_ids": [service.registry.curated_id],
            "uploads": {
                "max_file_size_mb": settings.uploads.max_file_size_mb,
                "max_documents_per_corpus": settings.uploads.max_documents_per_corpus,
                "max_total_size_mb": settings.uploads.max_total_size_mb,
                "max_pages_per_document": settings.uploads.max_pages_per_document,
                "allowed_extensions": list(settings.uploads.allowed_extensions),
            },
            "query": {
                "modes": ["hybrid"],  # CorpusService.query_corpus accepts only hybrid
                "default_mode": "hybrid",
                "top_k": {"min": 1, "max": QUERY_TOP_K_MAX, "default": settings.evidence.top_k},
                "max_question_chars": 2000,
                # worst case for a generate=true query: every LLM attempt runs to its timeout
                "timeout_seconds": llm.timeout_seconds * (llm.max_retries + 1),
            },
        }

    @app.post("/corpora", status_code=201)
    def create_corpus(body: CreateCorpus):
        return service.create_corpus(body.corpus_id, body.name).model_dump(mode="json")

    @app.get("/corpora")
    def list_corpora():
        return {"corpora": [c.model_dump(mode="json") for c in service.list_corpora()]}

    @app.get("/corpora/{corpus_id}")
    def get_corpus(corpus_id: str):
        return service.get_corpus(corpus_id).model_dump(mode="json")

    @app.delete("/corpora/{corpus_id}", status_code=204)
    def delete_corpus(corpus_id: str):
        service.delete_corpus(corpus_id)

    @app.post("/corpora/{corpus_id}/documents")
    async def upload(corpus_id: str, files: list[UploadFile] = File(...)):
        service.get_corpus(corpus_id)  # 404/422 before reading any upload
        limit = int(settings.uploads.max_file_size_mb * 1024 * 1024)
        payload = []
        for f in files:
            data = await f.read(limit + 1)  # never read more than one byte past the limit
            payload.append((f.filename, data))
        corpus, results = service.add_documents(corpus_id, payload)
        body = {"corpus": corpus.model_dump(mode="json"), "files": [r.to_dict() for r in results]}
        too_large = results and all(r.status == "rejected" and r.reason.startswith("The file is larger") for r in results)
        return JSONResponse(status_code=413 if too_large else 200, content=body)

    @app.post("/corpora/{corpus_id}/build", status_code=202)
    def build(corpus_id: str):
        return {"corpus": service.start_build(corpus_id).model_dump(mode="json"),
                "message": f"Build started; poll GET /corpora/{corpus_id} until status is 'ready' or 'failed'."}

    @app.post("/corpora/{corpus_id}/query")
    def query(corpus_id: str, body: QueryRequest):
        answer = service.query_corpus(corpus_id, body.question, mode=body.mode, top_k=body.top_k, generate=body.generate)
        return answer.model_dump(mode="json")

    @app.get("/", include_in_schema=False)
    def ui():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
