"""LLM provider abstraction (Phase 5; docs/DECISIONS.md D23).

The RAG pipeline depends only on `LLMClient.complete(LLMRequest) -> LLMResponse`.
Providers are registered in `PROVIDERS`; the first is `openai_compatible`, the
chat-completions protocol served by hosted APIs and by local servers (Ollama,
vLLM, llama.cpp server, LM Studio), so a hosted or local Qwen/Llama/Gemma model
(SRS §39) works without code changes. It uses only the standard library.

Safety rules enforced by `make_llm_client`:
* provider "none" (the default) means generation is disabled — nothing is sent;
* the API key is read from the environment variable named in llm.api_key_env and
  is never logged, echoed in errors, or stored;
* corpus evidence is sent to a non-localhost base_url only if llm.allow_remote is true.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from constitutional_evidence_rag.common.config import LLMSettings
from constitutional_evidence_rag.common.logging import get_logger

logger = get_logger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class LLMError(Exception):
    """Base class for LLM failures. Messages never contain the API key."""


class LLMConfigurationError(LLMError):
    """The LLM is not configured, or configured unsafely (missing key, remote host not allowed)."""


class LLMTimeoutError(LLMError):
    """The provider did not answer within llm.timeout_seconds."""


class LLMProviderError(LLMError):
    """The provider returned an error status or an unusable response envelope."""


@dataclass(frozen=True)
class LLMRequest:
    system: str
    user: str
    model: str
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    json_mode: bool = False


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    finish_reason: str | None = None


class LLMClient(Protocol):
    provider: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


class OpenAICompatibleClient:
    """POST {base_url}/chat/completions (OpenAI chat-completions wire format)."""

    provider = "openai_compatible"

    def __init__(self, base_url: str, api_key: str | None):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key  # kept private; never logged

    def __repr__(self) -> str:  # never expose the key through repr()
        return f"OpenAICompatibleClient(base_url={self.base_url!r}, api_key={'set' if self._api_key else 'unset'})"

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": request.model,
            "messages": [{"role": "system", "content": request.system}, {"role": "user", "content": request.user}],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        http_request = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode("utf-8"),
                                              headers=headers, method="POST")
        try:
            with urllib.request.urlopen(http_request, timeout=request.timeout_seconds) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace") if exc.fp else ""
            raise LLMProviderError(f"provider returned HTTP {exc.code}: {_redact(detail, self._api_key)}") from None
        except (TimeoutError, socket.timeout):
            raise LLMTimeoutError(f"no response within {request.timeout_seconds:g} s") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LLMTimeoutError(f"no response within {request.timeout_seconds:g} s") from None
            raise LLMProviderError(f"cannot reach provider at {self.base_url}: {exc.reason}") from None
        try:
            data = json.loads(payload)
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMProviderError("provider response is not a chat-completions envelope") from None
        if not isinstance(text, str):
            raise LLMProviderError("provider response has no text content")
        return LLMResponse(text=text, model=str(data.get("model") or request.model), finish_reason=choice.get("finish_reason"))


PROVIDERS = {"openai_compatible": OpenAICompatibleClient}


def _redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


def is_local_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in _LOCAL_HOSTS


def make_llm_client(settings: LLMSettings) -> LLMClient:
    """Build the configured client, or raise LLMConfigurationError explaining what is missing."""
    if settings.provider == "none":
        raise LLMConfigurationError(
            "LLM generation is not configured (llm.provider: none). Set llm.provider, llm.model and llm.base_url "
            "in configs/v1.yaml or LLM_PROVIDER / LLM_MODEL / LLM_BASE_URL, and the API key in the environment.")
    if not settings.model:
        raise LLMConfigurationError("llm.model is not set")
    if not settings.base_url or urlparse(settings.base_url).scheme not in ("http", "https"):
        raise LLMConfigurationError("llm.base_url must be an http(s) URL")
    if not is_local_url(settings.base_url) and not settings.allow_remote:
        raise LLMConfigurationError(
            f"llm.base_url {settings.base_url!r} is not localhost. Corpus evidence is sent to a remote provider only "
            "when llm.allow_remote is true — set it explicitly to confirm.")
    api_key = os.environ.get(settings.api_key_env) or None
    if settings.require_api_key and not api_key:
        raise LLMConfigurationError(
            f"environment variable {settings.api_key_env} is not set (llm.require_api_key is true). "
            "Put the key in .env or the environment, never in the config file.")
    logger.info("LLM provider %s, model %s, base_url %s, api key %s", settings.provider, settings.model,
                settings.base_url, "set" if api_key else "not used")
    return PROVIDERS[settings.provider](settings.base_url, api_key)
