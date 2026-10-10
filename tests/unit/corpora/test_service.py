"""Phase 7 service: build, isolation, querying (Phases 4-6 on user documents), failure safety."""
import hashlib

import pytest

from constitutional_evidence_rag.chunking.legal_chunker import CHUNKS_FILENAME, load_chunks
from constitutional_evidence_rag.common.answer import AnswerStatus
from constitutional_evidence_rag.common.evidence import EvidenceRole
from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, SourceType
from constitutional_evidence_rag.common.validation import SupportStatus
from constitutional_evidence_rag.corpora.errors import (
    CorpusBuildError, CorpusBusyError, CorpusNotFoundError, CorpusNotReadyError, ReservedCorpusError,
)
from constitutional_evidence_rag.corpora.models import CorpusStatus
from constitutional_evidence_rag.corpora.service import USER_CORPUS_NOTICE, CorpusService
from constitutional_evidence_rag.generation.answer import render_text
from constitutional_evidence_rag.generation.pipeline import AnswerPipeline, CorpusIsolationError
from constitutional_evidence_rag.retrieval.hybrid import Retriever
from constitutional_evidence_rag.retrieval.store import BM25_KIND, DENSE_KIND, QueryError

from corpora_fixtures import A_PAGES, embedder_factory, pdf_bytes
from llm_fakes import ScriptedLLM, reply


def retriever(service, corpus_id):
    p = service.settings.paths
    return Retriever.load(processed_root=p.data_processed_dir, indexes_root=p.indexes_dir, corpus_id=corpus_id,
                          settings=service.settings, embedder=service.embedder())


# ------------------------------------------------------------------ build


def test_build_produces_a_ready_corpus_with_its_own_artifacts(two_corpora):
    corpus = two_corpora.get_corpus("corpus-a")
    assert (corpus.status, corpus.document_count, corpus.error) == (CorpusStatus.READY, 1, None) and corpus.chunk_count >= 1
    reg = two_corpora.registry
    for kind in (BM25_KIND, DENSE_KIND):
        assert (reg.indexes_dir("corpus-a") / kind / "meta.json").exists()
    assert (reg.processed_dir("corpus-a") / CHUNKS_FILENAME).exists()
    assert not any(p.name.startswith(".") for p in reg.indexes_root.iterdir())  # no staging/backup left


def test_chunks_carry_their_corpus_and_pages(two_corpora):
    chunks = load_chunks(two_corpora.registry.processed_dir("corpus-a") / CHUNKS_FILENAME)
    assert {c.corpus_id for c in chunks} == {"corpus-a"} and {c.source_type for c in chunks} == {SourceType.USER_DOCUMENT}
    altitude = [c for c in chunks if "altitude" in c.text]
    assert altitude and altitude[0].page_start <= 2 <= altitude[0].page_end  # the page-2 sentence's chunk covers page 2
    assert all(c.article_number is None and c.case_name is None for c in chunks)


def test_long_documents_keep_page_ranges(service):
    pages = [" ".join(f"Page {n} sentence {i} about zeppelin mooring procedures." for i in range(60)) for n in range(1, 5)]
    service.create_corpus("long")
    service.add_documents("long", [("manual.pdf", pdf_bytes(pages))])
    service.build_corpus("long")
    chunks = load_chunks(service.registry.processed_dir("long") / CHUNKS_FILENAME)
    assert len(chunks) > 1 and chunks[0].page_start == 1 and chunks[-1].page_end == 4
    assert all(a.page_start <= b.page_start for a, b in zip(chunks, chunks[1:]))
    for c in chunks:  # each chunk's text really comes from its pages
        pages_named = {int(w) for w in __import__("re").findall(r"Page (\d+) sentence", c.text)}
        assert pages_named <= set(range(c.page_start, c.page_end + 1))


def test_curated_chunks_and_indexes_are_untouched_by_user_builds(two_corpora, curated):
    before = load_chunks(two_corpora.registry.processed_dir(CURATED_CORPUS_ID) / CHUNKS_FILENAME)
    two_corpora.add_documents("corpus-a", [("extra.pdf", pdf_bytes(["Extra zeppelin rules about mooring masts."]))])
    two_corpora.build_corpus("corpus-a")
    assert load_chunks(two_corpora.registry.processed_dir(CURATED_CORPUS_ID) / CHUNKS_FILENAME) == before == curated


# ------------------------------------------------------------------ isolation


def test_retrieval_isolation_both_ways(two_corpora):
    for mine, other, marker in (("corpus-a", "corpus-b", "zeppelin"), ("corpus-b", "corpus-a", "marmalade")):
        own = retriever(two_corpora, mine)
        foreign = retriever(two_corpora, other)
        for mode in ("bm25", "dense", "hybrid"):
            hits = getattr(own, mode)(f"{marker} rules")
            assert hits and {h.chunk.corpus_id for h in hits} == {mine}
            assert all(marker not in h.chunk.text.lower() for h in getattr(foreign, mode)(f"{marker} rules"))
            assert {h.chunk.corpus_id for h in getattr(foreign, mode)(f"{marker} rules")} <= {other}


def test_answer_evidence_and_citations_stay_in_the_corpus(two_corpora):
    answer = two_corpora.query_corpus("corpus-b", "What do the zeppelin hangar regulations require?")
    assert all(e.chunk.corpus_id == "corpus-b" for e in answer.evidence)
    assert all("zeppelin" not in e.chunk.text.lower() for e in answer.evidence)


def test_pipeline_refuses_foreign_evidence(two_corpora):
    r = retriever(two_corpora, "corpus-a")
    pipeline = AnswerPipeline(r, {}, two_corpora.settings, corpus_id="corpus-b")  # bound to the wrong corpus
    with pytest.raises(CorpusIsolationError):
        pipeline.answer("zeppelin hangar regulations")


# ------------------------------------------------------------------ querying user documents (Phases 4-6)


def test_extractive_answer_cites_file_and_page(two_corpora):
    answer = two_corpora.query_corpus("corpus-a", "How often must each airship be inspected under the zeppelin hangar regulations?")
    assert answer.status in (AnswerStatus.ANSWERED, AnswerStatus.PARTIAL)
    assert answer.evidence[0].role is EvidenceRole.USER_DOCUMENT and answer.evidence[0].document_title == "document_A.pdf"
    assert answer.notices[-1] == USER_CORPUS_NOTICE
    text = render_text(answer)
    assert "[1] document_A.pdf — Pages 1–2" in text and "not legal advice" in text  # both short pages form one chunk


def test_generation_and_validation_on_user_documents(service):
    service.create_corpus("air")
    service.add_documents("air", [("document_A.pdf", pdf_bytes(A_PAGES))])
    service.build_corpus("air")
    fake = ScriptedLLM(reply(direct=[("Zeppelin hangar regulations require an inspection of every airship every thirty days [E1].",
                                      ["E1"], "explicit")],
                             explanation=[("Crews may skip the flight log entirely [E1].", ["E1"], "explicit")]))
    service._llm_client_factory = lambda settings: fake
    answer = service.query_corpus("air", "What do the zeppelin hangar regulations require?", generate=True, top_k=3)

    assert len(fake.requests) == 1 and "uploaded document (user-supplied, unverified)" in fake.requests[0].user
    assert answer.citation_style == "evidence_id" and answer.citations[0].title == "document_A.pdf"
    assert [c.status for c in answer.validation.claims] == [SupportStatus.SUPPORTED, SupportStatus.INSUFFICIENT]
    assert answer.status is AnswerStatus.PARTIAL and len(answer.evidence) <= 3
    assert "[E1] document_A.pdf — Pages 1–2" in render_text(answer)


def test_curated_corpus_queries_through_the_service(service, curated):
    answer = service.query_corpus(CURATED_CORPUS_ID, "What is Article 21?")
    assert answer.evidence[0].chunk.article_number == "21" and USER_CORPUS_NOTICE not in answer.notices


@pytest.mark.parametrize("status,message", [(CorpusStatus.UPLOADED, "has not been built yet"),
                                            (CorpusStatus.PROCESSING, "is still processing."),
                                            (CorpusStatus.FAILED, "failed to build: boom")])
def test_not_ready_corpus_is_refused_before_any_index_is_touched(service, monkeypatch, status, message):
    service.create_corpus("pending")
    service.registry.update("pending", status=status, error="boom" if status is CorpusStatus.FAILED else None)
    monkeypatch.setattr(Retriever, "load", lambda *a, **k: (_ for _ in ()).throw(AssertionError("index touched")))
    with pytest.raises(CorpusNotReadyError, match=message):
        service.query_corpus("pending", "anything")


def test_missing_corpus_and_bad_query(two_corpora):
    with pytest.raises(CorpusNotFoundError, match="Corpus 'nope' does not exist."):
        two_corpora.query_corpus("nope", "anything")
    with pytest.raises(QueryError):
        two_corpora.query_corpus("corpus-a", "anything", mode="bm25")
    with pytest.raises(QueryError):
        two_corpora.query_corpus("corpus-a", "   ")


# ------------------------------------------------------------------ failure safety, background builds, reserved


def _digest(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.rglob("*")) if p.is_file()}


def test_failed_rebuild_keeps_the_previous_index_intact(two_corpora, monkeypatch):
    index = two_corpora.registry.indexes_dir("corpus-a")
    before = _digest(index)
    two_corpora.add_documents("corpus-a", [("extra.pdf", pdf_bytes(["Extra zeppelin rules about mooring masts."]))])
    import constitutional_evidence_rag.corpora.indexing as indexing
    monkeypatch.setattr(indexing, "build_dense_index", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("/secret/path exploded")))

    with pytest.raises(CorpusBuildError) as exc:
        two_corpora.build_corpus("corpus-a")
    corpus = two_corpora.get_corpus("corpus-a")
    assert corpus.status is CorpusStatus.FAILED and "/secret/path" not in corpus.error and "/secret/path" not in exc.value.message
    assert _digest(index) == before  # no partial index; previous index intact
    assert not any(p.name.startswith(".") for p in index.parent.iterdir())


def test_build_with_no_documents_fails_cleanly(service):
    service.create_corpus("empty")
    with pytest.raises(CorpusBuildError, match="no documents"):
        service.build_corpus("empty")
    assert service.get_corpus("empty").status is CorpusStatus.FAILED


def test_background_build_reaches_ready_and_blocks_a_second_build(service):
    service.create_corpus("bg")
    service.add_documents("bg", [("document_A.pdf", pdf_bytes(A_PAGES))])
    started = service.start_build("bg")
    assert started.status is CorpusStatus.PROCESSING
    with pytest.raises(CorpusBusyError):
        service.start_build("bg")
    assert service.wait_for_build("bg", timeout=60).status is CorpusStatus.READY


def test_rebuild_invalidates_the_cached_retriever(two_corpora):
    first = two_corpora.query_corpus("corpus-a", "mooring masts")
    two_corpora.add_documents("corpus-a", [("extra.pdf", pdf_bytes(["Extra zeppelin rules about mooring masts."]))])
    two_corpora.build_corpus("corpus-a")
    second = two_corpora.query_corpus("corpus-a", "mooring masts")
    assert any("mooring" in e.chunk.text for e in second.evidence)
    assert not any("mooring" in e.chunk.text for e in first.evidence)


def test_reserved_corpus_is_read_only(service, curated):
    for action in (lambda: service.add_documents(CURATED_CORPUS_ID, [("a.pdf", pdf_bytes(A_PAGES))]),
                   lambda: service.build_corpus(CURATED_CORPUS_ID), lambda: service.start_build(CURATED_CORPUS_ID),
                   lambda: service.delete_corpus(CURATED_CORPUS_ID)):
        with pytest.raises(ReservedCorpusError):
            action()


def test_embedding_model_is_loaded_once(settings):
    calls = []
    service = CorpusService(settings, embedder_factory=lambda s: calls.append(1) or embedder_factory())
    for corpus_id in ("x1", "x2"):
        service.create_corpus(corpus_id)
        service.add_documents(corpus_id, [("a.pdf", pdf_bytes(A_PAGES))])
        service.build_corpus(corpus_id)
        service.query_corpus(corpus_id, "zeppelin rules")
    assert calls == [1]


def test_startup_recovers_and_creates_directories(settings):
    service = CorpusService(settings, embedder_factory=embedder_factory)
    service.create_corpus("crashed")
    service.registry.update("crashed", status=CorpusStatus.PROCESSING)
    assert service.startup() == ["crashed"] and service.get_corpus("crashed").status is CorpusStatus.FAILED
    assert settings.paths.uploads_dir.is_dir() and settings.paths.indexes_dir.is_dir()
