"""Phase 6 integration: claim extraction, answer policy, pipeline, CLI, configuration.
Every LLM is scripted; validation itself never calls an LLM."""
import json
import runpy
from pathlib import Path

import pytest

from constitutional_evidence_rag.common.answer import Answer, AnswerClaim, AnswerStatus, ClaimBasis
from constitutional_evidence_rag.common.config import ConfigError, load_settings
from constitutional_evidence_rag.common.validation import ClaimType, SupportStatus
from constitutional_evidence_rag.generation.grounding import validate_answer
from constitutional_evidence_rag.validation.claims import extract_claims

from answer_fixtures import REPO_ROOT, HashingEmbedder, write_config
from llm_fakes import ART21, ScriptedLLM, llm_pipeline, reply

S, C, I = SupportStatus.SUPPORTED, SupportStatus.CONTRADICTED, SupportStatus.INSUFFICIENT
SUPPORTED = ("Article 21 protects life and personal liberty [E1].", ["E1"], "explicit")
UNSUPPORTED = ("Article 21 guarantees free legal aid to every prisoner [E1].", ["E1"], "explicit")
CONTRADICTED = ("Article 21 provides that personal liberty can be taken away without legal procedure [E1].", ["E1"], "explicit")


def run(built, *replies, query="What does Article 21 provide?", validation=None):
    fake = ScriptedLLM(*replies)
    return llm_pipeline(built, fake, validation=validation).answer(query), fake


# ------------------------------------------------------------------ claim extraction and citation mapping


def stmt(text, ids=(1,), basis=ClaimBasis.EXPLICIT):
    return AnswerClaim(claim_id="C1", section="answer", statement=text, citation_ids=list(ids), basis=basis)


def test_statement_is_split_into_claims_with_their_own_citations():
    claims = extract_claims(stmt("Article 21 protects life and personal liberty [E2]. The Supreme Court interpreted "
                                 "Article 21 broadly in Maneka Gandhi [E1].", (2, 1)))
    assert [(c.claim_id, c.text, c.citation_ids) for c in claims] == [
        ("C1.1", "Article 21 protects life and personal liberty.", ["E2"]),
        ("C1.2", "The Supreme Court interpreted Article 21 broadly in Maneka Gandhi.", ["E1"])]
    assert claims[1].claim_type is ClaimType.INTERPRETATIONAL


def test_marker_after_full_stop_stays_with_its_sentence():
    claims = extract_claims(stmt("Article 21 protects liberty. [E1] Article 14 ensures equality. [E2]", (1, 2)))
    assert [c.citation_ids for c in claims] == [["E1"], ["E2"]]


def test_unmarked_sentence_inherits_statement_citations_and_duplicates_collapse():
    claims = extract_claims(stmt("Article 21 protects liberty. It also protects life [E3] [E3] [E3].", (3,)))
    assert [c.citation_ids for c in claims] == [["E3"], ["E3"]]


def test_malformed_markers_are_not_citations():
    claims = extract_claims(stmt("Article 21 protects liberty [E 1] [Ex] [1].", (2,)))
    assert claims[0].citation_ids == ["E2"]  # only the statement's real citation; nothing invented


def test_claim_types():
    assert extract_claims(stmt('Article 21 says "No person shall be deprived of his life" [E1].'))[0].claim_type is ClaimType.QUOTATION
    assert extract_claims(stmt("Article 21 protects life [E1]."))[0].claim_type is ClaimType.PROPOSITIONAL
    assert extract_claims(stmt("These passages suggest a broad reading [E1].", basis=ClaimBasis.INFERENCE))[0].claim_type is ClaimType.INTERPRETATIONAL


# ------------------------------------------------------------------ answer policy


def test_supported_claims_pass_unchanged(built):
    a, fake = run(built, reply(direct=[SUPPORTED]))
    assert a.status is AnswerStatus.ANSWERED and a.claims[0].statement == SUPPORTED[0]
    assert [v.status for v in a.validation.claims] == [S] and a.validation.claims[0].kept
    assert len(fake.requests) == 1  # validation adds no LLM call


def test_mixed_statuses_give_a_partial_answer_with_flagged_contradiction(built):
    a, _ = run(built, reply(direct=[SUPPORTED], explanation=[UNSUPPORTED, CONTRADICTED]))

    assert a.status is AnswerStatus.PARTIAL
    assert [v.status for v in a.validation.claims] == [S, I, C]
    assert [c.statement for c in a.claims] == [SUPPORTED[0]]  # unsupported and contradicted never shown as fact
    assert any("contradicts its cited evidence" in n and "flagged" in n for n in a.notices)
    assert any("does not confirm it" in n for n in a.notices)
    validate_answer(a, _evidence_set(a))


def test_unsupported_sentence_is_removed_from_its_statement(built):
    mixed = ("Article 21 protects life and personal liberty. It also guarantees free legal aid to every prisoner [E1].", ["E1"], "explicit")
    a, _ = run(built, reply(direct=[mixed]))
    assert a.status is AnswerStatus.PARTIAL
    assert a.claims[0].statement == "Article 21 protects life and personal liberty [E1]."


def test_non_supporting_citation_is_dropped(built):
    # same reply as Phase 5's normalization test: [E2] does not support the claim
    a, _ = run(built, reply(direct=[("Article 21 protects personal liberty [e1, E2].", ["[E1]", "e2"], "explicit")]))
    assert a.claims[0].statement == "Article 21 protects personal liberty [E1]."
    assert [c.evidence_id for c in a.citations] == ["E1"]
    assert a.validation.claims[0].citation_ids == ["E1", "E2"] and a.validation.claims[0].supporting_ids == ["E1"]


def test_no_supported_claim_means_insufficient_evidence(built):
    a, _ = run(built, reply(direct=[UNSUPPORTED, CONTRADICTED]))
    assert a.status is AnswerStatus.INSUFFICIENT_EVIDENCE and not a.claims and not a.citations
    assert a.validation and len(a.validation.claims) == 2 and a.evidence
    assert any("No generated claim was supported" in n for n in a.notices)


def test_model_failures_and_refusals_are_not_validated(built):
    a, _ = run(built, reply(status="insufficient_evidence", reason="nothing relevant"))
    assert a.status is AnswerStatus.INSUFFICIENT_EVIDENCE and a.validation is None


# ------------------------------------------------------------------ compatibility, on/off


def test_validation_disabled_keeps_phase5_behaviour(built):
    on, _ = run(built, reply(direct=[SUPPORTED], explanation=[UNSUPPORTED]))
    off, _ = run(built, reply(direct=[SUPPORTED], explanation=[UNSUPPORTED]), validation={"enabled": False})
    assert off.validation is None and off.status is AnswerStatus.ANSWERED and len(off.claims) == 2
    assert on.validation is not None and on.status is AnswerStatus.PARTIAL and len(on.claims) == 1


def test_phase5_output_contract_is_preserved(built):
    a, _ = run(built, reply(direct=[(f'Article 21 provides that "{ART21}" [E1]', ["E1"], "explicit")]))
    summary = a.summary()
    assert {"answer", "citations", "evidence_used"} <= set(summary) and summary["citations"] == ["E1"]
    assert summary["validation"]["claims"][0]["status"] == "supported"
    assert Answer.model_validate_json(a.model_dump_json()) == a


def test_extractive_answers_are_untouched(pipeline):
    a = pipeline.answer("What is Article 21?")  # Phase 4 extractive backend
    assert a.validation is None and a.generator == "extractive-v1"


def test_semantic_method_reuses_the_supplied_embedder(built):
    from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
    from constitutional_evidence_rag.generation.pipeline import AnswerPipeline, make_generator
    from llm_fakes import llm_settings
    s = llm_settings(built["settings"], validation={"method": "semantic"})
    p = AnswerPipeline.load(processed_root=s.paths.data_processed_dir, indexes_root=s.paths.indexes_dir,
                            corpus_id=CURATED_CORPUS_ID, settings=s, embedder=HashingEmbedder(),
                            generator=make_generator(s, ScriptedLLM(reply(direct=[SUPPORTED]))))
    a = p.answer("What does Article 21 provide?")
    assert a.validation.method == "semantic" and a.validation.claims[0].semantic_score is not None


# ------------------------------------------------------------------ configuration


def test_repo_config_defaults():
    ev = load_settings(Path("configs/v1.yaml")).evidence_validation
    assert (ev.enabled, ev.method, ev.support_coverage, ev.semantic_support_threshold) == (True, "lexical", 0.75, 0.8)


def test_config_overrides_and_invalid_values(tmp_path):
    base = "app: {name: t, version: '0', phase: v1}\nlogging: {level: INFO}\npaths: {data_raw_dir: r, data_processed_dir: p}\n"
    good = tmp_path / "good.yaml"
    good.write_text(base + "evidence_validation: {enabled: false, method: semantic, support_coverage: 0.9}\n")
    ev = load_settings(good).evidence_validation
    assert (ev.enabled, ev.method, ev.support_coverage) == (False, "semantic", 0.9)
    for bad in ["{method: nli}", "{support_coverage: 0}", "{support_coverage: 1.5}", "{min_claim_terms: 0}", "{unknown: 1}"]:
        path = tmp_path / "bad.yaml"
        path.write_text(base + f"evidence_validation: {bad}\n")
        with pytest.raises(ConfigError):
            load_settings(path)


# ------------------------------------------------------------------ CLI


query_main = runpy.run_path(str(REPO_ROOT / "scripts" / "query.py"))["main"]


def cli(config, *args, llm):
    return query_main([*args, "--config", str(config)], embedder_factory=lambda s: HashingEmbedder(),
                      llm_client_factory=lambda s: llm)


def test_cli_generate_reports_validation(built, capsys):
    config = write_config(built["tmp"])
    fake = ScriptedLLM(reply(direct=[SUPPORTED], explanation=[CONTRADICTED]))
    assert cli(config, "What does Article 21 provide?", "--mode", "hybrid", "--top-k", "5", "--generate", llm=fake) == 0
    out = capsys.readouterr().out
    assert "Evidence-support check (basic, lexical; textual support only, not legal verification): 1 of 2 claims supported" in out
    assert "Flagged claims (contradicted by the cited evidence; not part of the answer):" in out
    assert "Claim validation:" not in out  # details only on request

    assert cli(config, "What does Article 21 provide?", "--generate", "--show-validation", llm=fake) == 0
    out = capsys.readouterr().out
    assert "Claim validation:" in out and "Support: SUPPORTED" in out and "Support: CONTRADICTED" in out and "Reason:" in out


def test_cli_json_includes_validation(built, capsys):
    config = write_config(built["tmp"])
    assert cli(config, "What does Article 21 provide?", "--generate", "--json", llm=ScriptedLLM(reply(direct=[SUPPORTED]))) == 0
    answer = Answer.model_validate(json.loads(capsys.readouterr().out))
    assert answer.validation.claims[0].status is S


def _evidence_set(answer):
    from constitutional_evidence_rag.common.evidence import EvidenceSet
    return EvidenceSet(query=answer.query, query_type=answer.query_type, items=answer.evidence, candidates_considered=len(answer.evidence))
