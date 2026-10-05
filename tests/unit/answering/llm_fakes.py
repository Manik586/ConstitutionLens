"""Scripted stand-in for an LLM provider: no network, records every request."""
from __future__ import annotations

import json

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.generation.llm import LLMResponse
from constitutional_evidence_rag.generation.pipeline import AnswerPipeline, make_generator

from answer_fixtures import HashingEmbedder


class ScriptedLLM:
    provider = "scripted"

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def complete(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, model=request.model)


def reply(direct=(), explanation=(), status="answered", reason=None) -> str:
    def stmts(items):
        return [{"text": t, "citations": list(c), "basis": b} for t, c, b in items]
    return json.dumps({"status": status, "direct_answer": stmts(direct), "explanation": stmts(explanation),
                       "insufficient_reason": reason})


ART21 = "No person shall be deprived of his life or personal liberty except according to procedure established by law."


def llm_settings(settings, generation=None, **llm):
    return settings.model_copy(update={
        "generation": settings.generation.model_copy(update={"backend": "llm", **(generation or {})}),
        "llm": settings.llm.model_copy(update={"model": "test-model", **llm})})


def llm_pipeline(built, llm, generation=None, **llm_overrides):
    s = llm_settings(built["settings"], generation, **llm_overrides)
    return AnswerPipeline.load(processed_root=s.paths.data_processed_dir, indexes_root=s.paths.indexes_dir,
                               corpus_id=CURATED_CORPUS_ID, settings=s, embedder=HashingEmbedder(),
                               generator=make_generator(s, llm))
