"""Phase 7 HTTP API (skipped when the optional [api] extra is not installed)."""
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from constitutional_evidence_rag.api.app import create_app  # noqa: E402
from constitutional_evidence_rag.corpora.models import CorpusStatus  # noqa: E402
from constitutional_evidence_rag.corpora.service import CorpusService  # noqa: E402

from corpora_fixtures import A_PAGES, B_PAGES, embedder_factory, pdf_bytes  # noqa: E402
from llm_fakes import ScriptedLLM, reply  # noqa: E402

SECRET = "sk-test-SECRET-never-shown"


@pytest.fixture
def api(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    service = CorpusService(settings, embedder_factory=embedder_factory)
    bodies = []
    with TestClient(create_app(settings, service), raise_server_exceptions=False) as client:
        original = client.request

        def recording(*args, **kwargs):
            response = original(*args, **kwargs)
            bodies.append(response.text)
            return response
        client.request = recording
        yield client, service
    for body in bodies:  # no server paths, no keys, no tracebacks — in any response
        assert str(tmp_path) not in body and SECRET not in body and "Traceback" not in body


def upload(client, corpus_id, *files):
    return client.post(f"/corpora/{corpus_id}/documents", files=[("files", (n, d, "application/pdf")) for n, d in files])


def wait_ready(client, corpus_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/corpora/{corpus_id}").json()["status"]
        if status in ("ready", "failed"):
            return status
        time.sleep(0.05)
    raise AssertionError("build did not finish")


def test_health(api):
    client, _ = api
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["default_corpus_id"] == "constitutional-core"


def test_create_list_get_and_errors(api):
    client, _ = api
    r = client.post("/corpora", json={"corpus_id": "my-research", "name": "My research"})
    assert r.status_code == 201 and r.json()["status"] == "uploaded"
    assert client.post("/corpora", json={"corpus_id": "my-research"}).json()["error"] == "corpus_exists"
    assert client.post("/corpora", json={"corpus_id": "my-research"}).status_code == 409
    assert client.post("/corpora", json={"corpus_id": "../etc"}).status_code == 422
    assert client.post("/corpora", json={"corpus_id": "constitutional-core"}).json()["error"] == "reserved_corpus"
    ids = [c["corpus_id"] for c in client.get("/corpora").json()["corpora"]]
    assert ids == ["constitutional-core", "my-research"]
    missing = client.get("/corpora/nope")
    assert missing.status_code == 404 and missing.json() == {"error": "corpus_not_found", "message": "Corpus 'nope' does not exist."}


def test_upload_build_poll_query(api):
    client, service = api
    client.post("/corpora", json={"corpus_id": "air"})
    r = upload(client, "air", ("document_A.pdf", pdf_bytes(A_PAGES)), ("bad.pdf", b"not a pdf"), ("../../evil.pdf", pdf_bytes(B_PAGES)))
    assert r.status_code == 200
    assert [(f["filename"], f["status"]) for f in r.json()["files"]] == [
        ("document_A.pdf", "accepted"), ("bad.pdf", "rejected"), ("evil.pdf", "accepted")]

    not_built = client.post("/corpora/air/query", json={"question": "zeppelin rules"})
    assert not_built.status_code == 409 and "has not been built yet" in not_built.json()["message"]

    assert client.post("/corpora/air/build").status_code == 202
    assert wait_ready(client, "air") == "ready"
    assert client.get("/corpora/air").json()["document_count"] == 2

    answer = client.post("/corpora/air/query", json={"question": "What do the zeppelin hangar regulations require?",
                                                     "mode": "hybrid", "top_k": 3}).json()
    assert answer["citations"] and answer["citations"][0]["title"] == "document_A.pdf"
    assert all(e["chunk"]["corpus_id"] == "air" for e in answer["evidence"])
    assert "uploaded documents only" in answer["notices"][-1]

    fake = ScriptedLLM(reply(direct=[("Zeppelin hangar regulations require an inspection of every airship every thirty days "
                                      "[E1].", ["E1"], "explicit")]))
    service._llm_client_factory = lambda settings: fake
    generated = client.post("/corpora/air/query", json={"question": "What do the zeppelin hangar regulations require?",
                                                        "generate": True}).json()
    assert generated["citation_style"] == "evidence_id" and generated["validation"]["claims"][0]["status"] == "supported"


def test_query_while_processing_is_503(api):
    client, service = api
    client.post("/corpora", json={"corpus_id": "busy"})
    service.registry.update("busy", status=CorpusStatus.PROCESSING)
    r = client.post("/corpora/busy/query", json={"question": "anything"})
    assert r.status_code == 503 and r.headers["Retry-After"] == "5"
    assert r.json() == {"error": "corpus_processing", "message": "Corpus 'busy' is still processing."}


def test_failed_build_reports_a_safe_reason(api):
    client, _ = api
    client.post("/corpora", json={"corpus_id": "empty"})
    assert client.post("/corpora/empty/build").status_code == 202
    assert wait_ready(client, "empty") == "failed"
    corpus = client.get("/corpora/empty").json()
    assert "no documents" in corpus["error"]
    r = client.post("/corpora/empty/query", json={"question": "anything"})
    assert r.status_code == 409 and "failed to build" in r.json()["message"]


def test_bad_requests(api):
    client, _ = api
    client.post("/corpora", json={"corpus_id": "c"})
    assert client.post("/corpora/c/query", json={"question": ""}).status_code == 422
    assert client.post("/corpora/c/query", json={"question": "x", "top_k": 0}).status_code == 422
    assert client.post("/corpora/c/query", json={"question": "x", "unexpected": 1}).json()["error"] == "invalid_request"
    assert upload(client, "constitutional-core", ("a.pdf", pdf_bytes(A_PAGES))).json()["error"] == "reserved_corpus"
    assert upload(client, "nope", ("a.pdf", pdf_bytes(A_PAGES))).status_code == 404


def test_oversize_upload_is_413(settings, tmp_path, monkeypatch):
    small = settings.model_copy(update={"uploads": settings.uploads.model_copy(update={"max_file_size_mb": 0.0005})})
    with TestClient(create_app(small, CorpusService(small, embedder_factory=embedder_factory))) as client:
        client.post("/corpora", json={"corpus_id": "c"})
        r = upload(client, "c", ("big.pdf", pdf_bytes(A_PAGES)))
        assert r.status_code == 413 and r.json()["files"][0]["status"] == "rejected"


def test_generate_without_a_configured_llm_is_503(api):
    client, service = api
    client.post("/corpora", json={"corpus_id": "air"})
    upload(client, "air", ("document_A.pdf", pdf_bytes(A_PAGES)))
    service.build_corpus("air")
    r = client.post("/corpora/air/query", json={"question": "zeppelin rules", "generate": True})  # provider: none
    assert r.status_code == 503 and r.json()["error"] == "generation_unavailable"


def test_unexpected_errors_are_generic_500(api, monkeypatch):
    client, service = api
    client.post("/corpora", json={"corpus_id": "c"})
    monkeypatch.setattr(service, "query_corpus", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("/srv/secret/path")))
    r = client.post("/corpora/c/query", json={"question": "anything"})
    assert r.status_code == 500 and r.json() == {"error": "internal_error", "message": "Internal error; the details are in the server log."}


def test_delete(api):
    client, _ = api
    client.post("/corpora", json={"corpus_id": "gone"})
    assert client.delete("/corpora/gone").status_code == 204
    assert client.get("/corpora/gone").status_code == 404
    assert client.delete("/corpora/constitutional-core").status_code == 409
