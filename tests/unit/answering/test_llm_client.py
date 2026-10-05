"""LLM provider abstraction: configuration safety and the OpenAI-compatible client,
exercised against a local HTTP server (no external API is ever called)."""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from constitutional_evidence_rag.common.config import LLMSettings
from constitutional_evidence_rag.generation.llm import (
    LLMConfigurationError, LLMProviderError, LLMRequest, LLMTimeoutError, OpenAICompatibleClient, make_llm_client,
)

SECRET = "sk-test-SECRET-value-123"


def settings(**kw):
    base = dict(provider="openai_compatible", model="m", base_url="http://localhost:9/v1")
    return LLMSettings(**{**base, **kw})


# ------------------------------------------------------------------ configuration


def test_disabled_by_default():
    with pytest.raises(LLMConfigurationError, match="not configured"):
        make_llm_client(LLMSettings())


@pytest.mark.parametrize("kw,match", [({"model": None}, "llm.model"), ({"base_url": "ftp://x"}, "http"),
                                      ({"base_url": None}, "http")])
def test_incomplete_configuration(kw, match, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    with pytest.raises(LLMConfigurationError, match=match):
        make_llm_client(settings(**kw))


def test_missing_api_key_names_the_variable_only(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LLMConfigurationError, match="LLM_API_KEY is not set"):
        make_llm_client(settings())
    monkeypatch.setenv("MY_KEY", SECRET)
    client = make_llm_client(settings(api_key_env="MY_KEY"))
    assert SECRET not in repr(client)


def test_local_server_without_key_is_allowed(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert make_llm_client(settings(require_api_key=False)).provider == "openai_compatible"


def test_remote_provider_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    with pytest.raises(LLMConfigurationError, match="allow_remote"):
        make_llm_client(settings(base_url="https://api.example.com/v1"))
    assert make_llm_client(settings(base_url="https://api.example.com/v1", allow_remote=True))


def test_api_key_never_logged(monkeypatch, caplog):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    with caplog.at_level(logging.DEBUG):
        make_llm_client(settings())
    assert SECRET not in caplog.text and "api key set" in caplog.text


# ------------------------------------------------------------------ HTTP client against a local server


class _Handler(BaseHTTPRequestHandler):
    behaviour, seen = "ok", []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if _Handler.behaviour == "slow":
            time.sleep(1.5)
        if _Handler.behaviour == "error":
            self.send_response(500); self.end_headers(); self.wfile.write(f"upstream failure {SECRET}".encode()); return
        payload = b"not json" if _Handler.behaviour == "garbage" else json.dumps(
            {"model": "served-model", "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _Handler.seen.clear()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


REQ = LLMRequest(system="sys", user="usr", model="m", temperature=0.0, max_output_tokens=50, timeout_seconds=1.0, json_mode=True)


def test_request_format_and_response(server):
    _Handler.behaviour = "ok"
    response = OpenAICompatibleClient(server, SECRET).complete(REQ)

    assert (response.text, response.model, response.finish_reason) == ("hello", "served-model", "stop")
    sent = _Handler.seen[0]
    assert sent["path"] == "/v1/chat/completions" and sent["auth"] == f"Bearer {SECRET}"
    assert sent["body"]["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert (sent["body"]["temperature"], sent["body"]["max_tokens"], sent["body"]["response_format"]) == (0.0, 50, {"type": "json_object"})


def test_http_error_is_a_provider_error_without_the_key(server):
    _Handler.behaviour = "error"
    with pytest.raises(LLMProviderError, match="HTTP 500") as exc:
        OpenAICompatibleClient(server, SECRET).complete(REQ)
    assert SECRET not in str(exc.value)


def test_timeout(server):
    _Handler.behaviour = "slow"
    with pytest.raises(LLMTimeoutError):
        OpenAICompatibleClient(server, None).complete(REQ)


def test_unusable_envelope(server):
    _Handler.behaviour = "garbage"
    with pytest.raises(LLMProviderError, match="envelope"):
        OpenAICompatibleClient(server, None).complete(REQ)


def test_unreachable_provider():
    with pytest.raises(LLMProviderError, match="cannot reach"):
        OpenAICompatibleClient("http://127.0.0.1:9/v1", None).complete(REQ)
