"""Phase 8: the UI is served by the FastAPI app without changing any API route (D30)."""
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from constitutional_evidence_rag.api.app import STATIC_DIR, UI_SECURITY_HEADERS, create_app  # noqa: E402
from constitutional_evidence_rag.corpora.service import CorpusService  # noqa: E402

from corpora_fixtures import embedder_factory, make_settings  # noqa: E402

SECRET = "sk-test-SECRET-never-shown"
JS_FILES = ["api.js", "state.js", "render.js", "main.js", "motion.js"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    settings = make_settings(tmp_path)
    service = CorpusService(settings, embedder_factory=embedder_factory)
    with TestClient(create_app(settings, service), raise_server_exceptions=False) as c:
        yield c, settings


def test_index_is_served_with_security_headers(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<title>ConstitutionLens</title>" in r.text and "Evidence-Grounded Document Research" in r.text
    for name, value in UI_SECURITY_HEADERS.items():
        assert r.headers[name] == value
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "unsafe-inline" not in csp


@pytest.mark.parametrize("path, media", [
    ("/static/app.css", "text/css"),
    *[(f"/static/js/{name}", "text/javascript") for name in JS_FILES],
    ("/static/vendor/gsap.min.js", "text/javascript"),
    ("/static/favicon.svg", "image/svg+xml"),
])
def test_static_assets_are_served_with_headers(client, path, media):
    c, _ = client
    r = c.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(media)  # ES modules need a JavaScript MIME type
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == UI_SECURITY_HEADERS["Content-Security-Policy"]
    assert r.headers["referrer-policy"] == "no-referrer"


def test_missing_static_file_is_404_not_the_page(client):
    c, _ = client
    assert c.get("/static/nope.js").status_code == 404


def test_health_is_unchanged(client):
    c, settings = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": settings.app.version, "default_corpus_id": "constitutional-core"}
    assert "content-security-policy" not in r.headers  # API responses keep their Phase 7 headers


def test_every_pre_existing_api_route_still_responds_as_before(client):
    c, _ = client
    routes = {(m, r.path) for r in c.app.routes for m in getattr(r, "methods", set()) or set()}
    for expected in [("GET", "/health"), ("POST", "/corpora"), ("GET", "/corpora"), ("GET", "/corpora/{corpus_id}"),
                     ("DELETE", "/corpora/{corpus_id}"), ("POST", "/corpora/{corpus_id}/documents"),
                     ("POST", "/corpora/{corpus_id}/build"), ("POST", "/corpora/{corpus_id}/query")]:
        assert expected in routes

    def is_json(r):
        return r.headers["content-type"].startswith("application/json")

    r = c.get("/corpora")
    assert r.status_code == 200 and is_json(r) and r.json()["corpora"][0]["corpus_id"] == "constitutional-core"
    r = c.post("/corpora", json={"corpus_id": "web-a", "name": "Web A"})
    assert r.status_code == 201 and r.json()["corpus_id"] == "web-a"
    assert c.get("/corpora/web-a").json()["status"] == "uploaded"
    r = c.get("/corpora/missing-one")
    assert r.status_code == 404 and is_json(r) and r.json()["error"] == "corpus_not_found"
    assert c.post("/corpora", json={"corpus_id": "web-a"}).status_code == 409
    assert c.post("/corpora", json={"corpus_id": "Bad ID"}).status_code == 422
    assert c.delete("/corpora/constitutional-core").status_code == 409
    assert c.post("/corpora/constitutional-core/build").status_code == 409
    r = c.post("/corpora/web-a/query", json={"question": "anything"})
    assert r.status_code == 409 and is_json(r)
    assert c.post("/corpora/web-a/query", json={"question": "x", "mode": "bm25"}).status_code in (409, 422)
    assert c.delete("/corpora/web-a").status_code == 204
    # No API path falls through to the HTML page.
    for path in ("/corpora", "/health", "/config"):
        assert is_json(c.get(path))


def test_config_exposes_only_ui_facts(client):
    c, settings = client
    r = c.get("/config")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"generation_available", "default_corpus_id", "reserved_corpus_ids", "uploads", "query"}
    assert body["generation_available"] is False  # llm.provider: none in the test settings
    assert body["default_corpus_id"] == "constitutional-core"
    assert body["reserved_corpus_ids"] == ["constitutional-core"]
    assert body["uploads"] == {
        "max_file_size_mb": settings.uploads.max_file_size_mb,
        "max_documents_per_corpus": settings.uploads.max_documents_per_corpus,
        "max_total_size_mb": settings.uploads.max_total_size_mb,
        "max_pages_per_document": settings.uploads.max_pages_per_document,
        "allowed_extensions": settings.uploads.allowed_extensions,
    }
    q = body["query"]
    assert q["modes"] == ["hybrid"] and q["default_mode"] == "hybrid"
    assert q["top_k"] == {"min": 1, "max": 20, "default": settings.evidence.top_k}
    assert q["timeout_seconds"] == settings.llm.timeout_seconds * (settings.llm.max_retries + 1)


def test_config_contains_no_secrets_or_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    settings = make_settings(tmp_path)
    llm = settings.llm.model_copy(update={"provider": "openai_compatible", "model": "secret-model-name",
                                          "base_url": "http://localhost:9/v1-secret-path"})
    settings = settings.model_copy(update={"llm": llm})
    service = CorpusService(settings, embedder_factory=embedder_factory)
    with TestClient(create_app(settings, service)) as c:
        r = c.get("/config")
    text = r.text
    assert r.json()["generation_available"] is True  # a localhost provider with a key is usable
    for leaked in (SECRET, "secret-model-name", "v1-secret-path", "localhost:9", str(tmp_path), str(Path.cwd()),
                   "LLM_API_KEY", "api_key", "base_url"):
        assert leaked not in text
    assert not re.search(r"[A-Za-z]:\\\\|/home/|/Users/|/app/", text)


def test_ui_source_has_no_inline_script_style_or_unsafe_sinks():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> would violate the CSP"
    assert not re.search(r"\sstyle=", html) and "<style" not in html
    assert not re.search(r"\son[a-z]+=", html), "inline event handlers are not allowed"
    assert "https://" not in html and "http://" not in html, "no CDN assets"
    for name in JS_FILES:
        source = (STATIC_DIR / "js" / name).read_text(encoding="utf-8")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert sink not in source, f"{name} uses {sink}"
        if name != "api.js":
            assert "fetch(" not in source, f"only api.js may call fetch ({name})"


def test_static_assets_are_package_data():
    # Served from inside the installed package (the Docker image installs from src/).
    package_root = Path(__file__).resolve().parents[3] / "src" / "constitutional_evidence_rag"
    assert STATIC_DIR == package_root / "web" / "static"
    assert (package_root / "web" / "__init__.py").exists()
