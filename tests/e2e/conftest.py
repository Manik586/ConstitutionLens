"""Phase 8 browser tests (Playwright + Chromium; marker `e2e`).

A real uvicorn server (the FastAPI app with the deterministic test embedder and no LLM)
serves the UI. Mocked tests intercept the API with page.route so responses are exact and
repeatable; the real end-to-end test talks to the same server unmocked.

Setup:  pip install -e ".[api,dev,e2e]"  &&  python -m playwright install chromium
"""
from __future__ import annotations

import json
import re
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("playwright")
import uvicorn  # noqa: E402

CORPORA_TESTS = Path(__file__).resolve().parents[1] / "unit" / "corpora"
if str(CORPORA_TESTS) not in sys.path:
    sys.path.insert(0, str(CORPORA_TESTS))

from constitutional_evidence_rag.api.app import create_app  # noqa: E402
from constitutional_evidence_rag.corpora.service import CorpusService  # noqa: E402

from corpora_fixtures import embedder_factory, make_settings  # noqa: E402

SECRET = "sk-e2e-SECRET-never-shown"


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests" in item.path.parts and "e2e" in item.path.parts:
            item.add_marker(pytest.mark.e2e)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, root: Path):
        self.root = root
        self.settings = make_settings(root)
        self.service = CorpusService(self.settings, embedder_factory=embedder_factory)
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(create_app(self.settings, self.service), host="127.0.0.1", port=self.port,
                                log_level="warning", lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.time() + 20
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.05)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    import os
    os.environ["LLM_API_KEY"] = SECRET  # must never reach the browser
    server = LiveServer(tmp_path_factory.mktemp("e2e-server"))
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    # Deterministic: no decorative motion (GSAP is skipped under reduced motion).
    return {**browser_context_args, "reduced_motion": "reduce", "viewport": {"width": 1200, "height": 900}}


@pytest.fixture
def page_errors(page):
    """Fail the test on any uncaught JS error or CSP violation."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"console: {msg.text}")
            if msg.type == "error" and ("Content Security Policy" in msg.text or "Uncaught" in msg.text) else None)
    yield errors
    assert not errors, errors


# ---------------------------------------------------------------- mocked API

def json_response(route, status: int, body) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


class MockApi:
    """Scriptable stand-in for the Phase 7 API. Records every request."""

    def __init__(self, config: dict, corpora: list[dict]):
        self.config = config
        self.corpora = corpora
        self.requests: list[dict] = []
        self.corpus_script: dict[str, list] = {}   # GET /corpora/{id}: queued responses (dict | "network" | (status, body))
        self.create_response = None                # (status, body) or None -> echo a new corpus
        self.upload_response = (200, None)
        self.build_response = (202, None)
        self.delete_response = (204, None)
        self.query_responses: list = []            # queued (status, body) | "network" | "hold"
        self.held: list = []

    def install(self, page, base_url: str):
        pattern = re.compile(re.escape(base_url) + r"/(config|health|corpora)(/.*)?(\?.*)?$")
        page.route(pattern, self.handle)

    def count(self, method: str, path: str) -> int:
        return sum(1 for r in self.requests if r["method"] == method and r["path"] == path)

    def find(self, method: str, path: str) -> list[dict]:
        return [r for r in self.requests if r["method"] == method and r["path"] == path]

    def _corpus(self, cid):
        return next((c for c in self.corpora if c["corpus_id"] == cid), None)

    def _respond(self, route, item):
        if item == "network":
            route.abort("internetdisconnected")
        elif item == "hold":
            self.held.append(route)
        else:
            status, body = item
            if status == 204:
                route.fulfill(status=204, body="")
            else:
                json_response(route, status, body)

    def handle(self, route, request):
        path = urlparse(request.url).path
        entry = {"method": request.method, "path": path, "headers": request.headers}
        body = request.post_data_buffer
        entry["raw"] = body
        if body and "json" in (request.headers.get("content-type") or ""):
            entry["json"] = json.loads(body)
        self.requests.append(entry)
        m = request.method
        parts = path.strip("/").split("/")

        if path == "/config":
            return json_response(route, 200, self.config)
        if path == "/health":
            return json_response(route, 200, {"status": "ok", "version": "test", "default_corpus_id": "constitutional-core"})
        if path == "/corpora" and m == "GET":
            return json_response(route, 200, {"corpora": self.corpora})
        if path == "/corpora" and m == "POST":
            if self.create_response:
                return self._respond(route, self.create_response)
            data = entry["json"]
            new = corpus(data["corpus_id"], data.get("name") or data["corpus_id"], "uploaded", 0, 0)
            self.corpora.append(new)
            return json_response(route, 201, new)
        cid = parts[1]
        if len(parts) == 2 and m == "GET":
            script = self.corpus_script.get(cid)
            if script:
                item = script.pop(0)
                if isinstance(item, dict):
                    self._replace(item)
                    return json_response(route, 200, item)
                return self._respond(route, item)
            c = self._corpus(cid)
            return json_response(route, 200, c) if c else json_response(route, 404, {"error": "corpus_not_found", "message": f"Corpus '{cid}' was not found."})
        if len(parts) == 2 and m == "DELETE":
            if self.delete_response[0] == 204:
                self.corpora = [c for c in self.corpora if c["corpus_id"] != cid]
            return self._respond(route, self.delete_response)
        if parts[-1] == "documents":
            status, payload = self.upload_response
            if isinstance(payload, dict) and isinstance(payload.get("corpus"), dict):
                self._replace(payload["corpus"])  # like the real service: the upload changes the stored status
            return self._respond(route, (status, payload))
        if parts[-1] == "build":
            status, payload = self.build_response
            if status == 202:
                c = dict(self._corpus(cid), status="processing")
                self._replace(c)
                payload = {"corpus": c, "message": "Build started."}
            return self._respond(route, (status, payload))
        if parts[-1] == "query":
            item = self.query_responses.pop(0) if self.query_responses else (500, {"error": "internal_error", "message": "no scripted answer"})
            return self._respond(route, item)
        return json_response(route, 404, {"error": "not_found", "message": "Not found."})

    def _replace(self, c):
        self.corpora = [c if x["corpus_id"] == c["corpus_id"] else x for x in self.corpora]


def advance(page, ms: int, step: int = 250) -> None:
    """Advance the page's fake clock in small steps. Each poll's fetch completes in real time,
    so jumping far ahead in one go would skip the timers it schedules afterwards."""
    remaining = ms
    while remaining > 0:
        page.clock.run_for(min(step, remaining))
        page.wait_for_timeout(15)
        remaining -= step


def corpus(cid, name, status, docs, chunks, *, reserved=False, error=None):
    return {"corpus_id": cid, "name": name, "status": status, "document_count": docs, "chunk_count": chunks,
            "created_at": "2026-10-01T09:30:00Z", "updated_at": "2026-10-01T09:30:00Z", "error": error,
            "owner_id": None, "reserved": reserved}


def default_config(generation=False):
    return {
        "generation_available": generation,
        "default_corpus_id": "constitutional-core",
        "reserved_corpus_ids": ["constitutional-core"],
        "uploads": {"max_file_size_mb": 20, "max_documents_per_corpus": 20, "max_total_size_mb": 200,
                    "max_pages_per_document": 500, "allowed_extensions": [".pdf"]},
        "query": {"modes": ["hybrid"], "default_mode": "hybrid", "top_k": {"min": 1, "max": 20, "default": 6},
                  "max_question_chars": 2000, "timeout_seconds": 120},
    }


@pytest.fixture
def mock_api(page, live_server):
    api = MockApi(default_config(), [
        corpus("constitutional-core", "Constitution of India and Supreme Court judgments (curated)", "ready", 17, 5477, reserved=True),
        corpus("briefs", "Case briefs", "ready", 3, 12),
    ])
    api.install(page, live_server.url)
    return api


@pytest.fixture
def open_app(page, live_server, page_errors):
    def _open(select: str | None = None, *, clock: bool = False):
        page.add_init_script("try { localStorage.clear(); } catch (e) {}")
        if clock:
            page.clock.install()
        page.goto(live_server.url + "/")
        page.wait_for_selector("#corpusList .corpus-item")
        if select:
            page.locator(f'.corpus-item[data-corpus-id="{select}"]').click()
        return page
    return _open
