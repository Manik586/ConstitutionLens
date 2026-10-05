import json
import runpy

import pytest

from constitutional_evidence_rag.common.answer import Answer, AnswerStatus
from constitutional_evidence_rag.generation.llm import make_llm_client

from answer_fixtures import REPO_ROOT, HashingEmbedder, write_config
from llm_fakes import ART21, ScriptedLLM, reply

query_main = runpy.run_path(str(REPO_ROOT / "scripts" / "query.py"))["main"]
EMB = lambda settings: HashingEmbedder()
GOOD = reply(direct=[(f'Article 21 provides that "{ART21}" [E1]', ["E1"], "explicit")])


@pytest.fixture
def config(built):
    return write_config(built["tmp"])


def run(config, *args, llm=None):
    fake = llm or ScriptedLLM(GOOD)
    code = query_main([*args, "--config", str(config)], embedder_factory=EMB, llm_client_factory=lambda s: fake)
    return code, fake


def test_generate_prints_a_cited_answer(config, capsys):
    code, fake = run(config, "What protections are provided for personal liberty?", "--mode", "hybrid", "--top-k", "5", "--generate")
    out = capsys.readouterr().out

    assert code == 0 and len(fake.requests) == 1
    assert "generator: llm/scripted/" in out and "[E1]" in out and "Citations:" in out and "not legal advice" in out


def test_generate_json_and_top_k(config, capsys):
    code, fake = run(config, "What does Article 21 provide?", "--top-k", "3", "--generate", "--json")
    answer = Answer.model_validate(json.loads(capsys.readouterr().out))

    assert code == 0 and answer.citation_style == "evidence_id" and len(answer.evidence) == 3
    assert fake.requests[0].user.count("\nSource: ") == 3  # all three selected items were sent


def test_generate_requires_hybrid_mode(config, caplog):
    assert run(config, "Article 21", "--mode", "bm25", "--generate")[0] == 2
    assert "--mode hybrid" in caplog.text


def test_generate_without_a_configured_llm_fails_fast(config, caplog):
    code = query_main(["What does Article 21 provide?", "--generate", "--config", str(config)],
                      embedder_factory=EMB, llm_client_factory=make_llm_client)  # repo config: provider none
    assert code == 2 and "not configured" in caplog.text


def test_missing_api_key_is_reported_by_name_only(config, caplog, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    code = query_main(["What does Article 21 provide?", "--generate", "--config", str(config)],
                      embedder_factory=EMB, llm_client_factory=make_llm_client)
    assert code == 2 and "LLM_API_KEY is not set" in caplog.text


def test_provider_failure_still_exits_cleanly_with_evidence(config, capsys):
    from constitutional_evidence_rag.generation.llm import LLMProviderError
    code, _ = run(config, "What does Article 21 provide?", "--generate", llm=ScriptedLLM(LLMProviderError("HTTP 503")))
    out = capsys.readouterr().out
    assert code == 0 and "generation_failed" in out and "Evidence considered" in out


def test_retrieval_only_and_extractive_answer_are_unchanged(config, capsys):
    fake = ScriptedLLM(GOOD)
    for args in (["Article 21", "--mode", "bm25", "--top-k", "5"], ["personal liberty", "--mode", "dense", "--top-k", "5"],
                 ["Article 21", "--mode", "hybrid", "--top-k", "5"]):
        assert query_main([*args, "--config", str(config)], embedder_factory=EMB, llm_client_factory=lambda s: fake) == 0
        assert capsys.readouterr().out.startswith(" 1. [")
    assert query_main(["What is Article 21?", "--answer", "--config", str(config)], embedder_factory=EMB,
                      llm_client_factory=lambda s: fake) == 0
    assert "generator: extractive-v1" in capsys.readouterr().out
    assert fake.requests == []  # the LLM is never called outside --generate
