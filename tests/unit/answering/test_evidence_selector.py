import pytest

from constitutional_evidence_rag.common.config import EvidenceSettings
from constitutional_evidence_rag.common.evidence import EvidenceOrigin, EvidenceRole, QueryType
from constitutional_evidence_rag.common.models import SourceType
from constitutional_evidence_rag.common.retrieval import RetrievalMethod, RetrievedChunk
from constitutional_evidence_rag.query.understanding import CaseIndex, analyze_query
from constitutional_evidence_rag.reranking.evidence_selector import assess_confidence, mentions_article, select_evidence

from answer_fixtures import CONST, KESAVA, MANEKA, TITLES, WRIT, registry_rows

REGISTRY = {(m.document_id, m.document_version): m for m in registry_rows()}
CASES = CaseIndex((d, t) for d, (t, st, _) in TITLES.items() if st is SourceType.JUDGMENT)
SETTINGS = EvidenceSettings()


def hits(chunks, order):
    """RetrievedChunk list in the given chunk order (hybrid ranks 1..n, RRF-like scores)."""
    return [RetrievedChunk(chunk=chunks[i], method=RetrievalMethod.HYBRID, rank=r, score=1 / (60 + r),
                           bm25_rank=r, bm25_score=10.0 - r, dense_rank=r + 1, dense_score=0.9 - r / 100)
            for r, i in enumerate(order, start=1)]


def select(chunks, query, order, settings=SETTINGS):
    analysis = analyze_query(query, CASES)
    return analysis, select_evidence(analysis, hits(chunks, order), chunks, REGISTRY, settings)


def const_index(chunks, article):
    return next(i for i, c in enumerate(chunks) if c.article_number == article)


@pytest.mark.parametrize("article", ["14", "21", "32"])
def test_provision_ranks_ahead_of_judgments_retrieved_above_it(chunks, article):
    judgments = [i for i, c in enumerate(chunks) if c.source_type is SourceType.JUDGMENT]
    _, ev = select(chunks, f"What is Article {article}?", judgments + [const_index(chunks, article)])  # provision retrieved last

    first = ev.items[0]
    assert first.chunk.article_number == article and first.role is EvidenceRole.CONSTITUTIONAL_TEXT
    assert first.boosts["provision_match"] == SETTINGS.provision_match_boost
    assert any(i.role is EvidenceRole.JUDICIAL for i in ev.items[1:])  # judgments stay as supporting evidence


@pytest.mark.parametrize("article", ["14", "21", "32"])
def test_provision_is_looked_up_when_retrieval_missed_it(chunks, article):
    judgments = [i for i, c in enumerate(chunks) if c.source_type is SourceType.JUDGMENT]
    _, ev = select(chunks, f"What does Article {article} provide?", judgments)  # provision not retrieved at all

    first = ev.items[0]
    assert first.chunk.article_number == article and first.origin is EvidenceOrigin.PROVISION_LOOKUP
    assert first.hybrid_rank is None and first.bm25_score is None  # no invented retrieval scores


@pytest.mark.parametrize("article", ["14", "21", "32"])
def test_interpretation_puts_judgments_first_and_keeps_the_provision(chunks, article):
    order = [const_index(chunks, article)] + [i for i, c in enumerate(chunks) if c.source_type is SourceType.JUDGMENT]
    _, ev = select(chunks, f"How has the Supreme Court interpreted Article {article}?", order)  # provision retrieved first

    assert ev.items[0].role is EvidenceRole.JUDICIAL
    provision = [i for i in ev.items if i.chunk.article_number == article]
    assert provision and provision[0].rank > 1


def test_interpretation_reserves_a_slot_for_the_provision(chunks):
    judgments = [i for i, c in enumerate(chunks) if c.source_type is SourceType.JUDGMENT]
    _, ev = select(chunks, "How has the Supreme Court interpreted Article 21?", judgments,
                   EvidenceSettings(top_k=3, max_per_document=5))

    assert len(ev.items) == 3 and ev.items[-1].chunk.article_number == "21"


def test_case_query_prioritizes_the_named_judgment(chunks):
    order = [i for i, c in enumerate(chunks) if c.document_id != MANEKA] + [i for i, c in enumerate(chunks) if c.document_id == MANEKA]
    _, ev = select(chunks, "What did Maneka Gandhi v. Union of India decide?", order)

    named = [i.chunk.document_id == MANEKA for i in ev.items]
    assert named[0] and named == sorted(named, reverse=True)  # all named-case items come first


def test_case_query_without_content_terms_leads_with_the_headnote(chunks):
    order = [i for i, c in enumerate(chunks) if c.document_id != KESAVA]
    _, ev = select(chunks, "What did Kesavananda Bharati establish?", order)

    assert ev.items[0].chunk.document_id == KESAVA and ev.items[0].chunk.heading == "HELD"
    assert ev.items[0].origin is EvidenceOrigin.CASE_LOOKUP


def test_provenance_and_original_scores_survive_selection(chunks):
    retrieved = hits(chunks, list(range(len(chunks))))
    by_id = {r.chunk_id: r for r in retrieved}
    analysis = analyze_query("What is Article 21?", CASES)
    ev = select_evidence(analysis, retrieved, chunks, REGISTRY, SETTINGS)

    for item in ev.items:
        r = by_id[item.chunk.chunk_id]
        assert item.chunk == r.chunk  # document_id, pages, chunk_id, source_type, case_name … unchanged
        assert (item.hybrid_rank, item.hybrid_score, item.bm25_rank, item.bm25_score, item.dense_rank, item.dense_score) == \
               (r.rank, r.score, r.bm25_rank, r.bm25_score, r.dense_rank, r.dense_score)
        assert item.final_score == pytest.approx(r.score + sum(item.boosts.values()))
        assert item.document_title == TITLES[item.chunk.document_id][0]
    assert [i.evidence_id for i in ev.items] == [f"E{n}" for n in range(1, len(ev.items) + 1)]


def test_source_type_never_enters_the_score(chunks):
    judgment = chunks[3]
    twin = judgment.model_copy(update={"chunk_id": "CONST-00000000c0c0@v1:0099:twin0000", "document_id": CONST,
                                       "source_type": SourceType.CONSTITUTIONAL_TEXT, "case_name": None,
                                       "opinion_author": None, "division": None})
    analysis = analyze_query("procedure right just fair", CASES)  # no locator in the question
    ev = select_evidence(analysis, hits([judgment, twin], [0, 1]) , [judgment, twin], REGISTRY, SETTINGS)

    scores = {i.chunk.source_type: i.final_score - i.hybrid_score for i in ev.items}
    assert scores == {SourceType.JUDGMENT: 0.0, SourceType.CONSTITUTIONAL_TEXT: 0.0}  # no boost for either type


def test_caps_and_top_k(chunks):
    _, ev = select(chunks, "Article 14 Article 21 Article 32", list(range(len(chunks))),
                   EvidenceSettings(top_k=5, max_per_document=1, max_constitutional=1))

    assert len(ev.items) <= 5
    assert sum(i.role is EvidenceRole.CONSTITUTIONAL_TEXT for i in ev.items) == 1
    judges = [i.chunk.document_id for i in ev.items if i.role is EvidenceRole.JUDICIAL]
    assert len(judges) == len(set(judges))


def test_missing_article_and_unknown_case_are_reported(chunks):
    analysis, ev = select(chunks, "What is Article 368?", [3, 4])
    assert "No constitutional text for Article 368 was found in the corpus." in ev.notices
    analysis, ev = select(chunks, "What did Golaknath v. State of Punjab hold?", [3, 4])
    assert "The case named in the question is not in the corpus." in assess_confidence(analysis, ev, SETTINGS)


def test_confidence_thresholds(chunks):
    analysis, ev = select(chunks, "capital of France", [0, 3])
    assert any("key terms" in p for p in assess_confidence(analysis, ev, SETTINGS))
    analysis, ev = select(chunks, "What is Article 21?", [1, 3])
    assert assess_confidence(analysis, ev, SETTINGS) == []
    assert assess_confidence(analysis, ev, EvidenceSettings(min_dense_score=0.99))  # configurable floor


def test_mentions_article():
    assert mentions_article("as held under Articles 14, 19 and 21 of the", "21")
    assert mentions_article("Art. 21 requires", "21")
    assert not mentions_article("paragraph 21 of the report", "21")
    assert not mentions_article("Article 214 applies", "21")
