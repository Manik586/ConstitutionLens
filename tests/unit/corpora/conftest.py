import pytest

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.corpora.service import CorpusService
from constitutional_evidence_rag.retrieval.bm25 import BM25Index
from constitutional_evidence_rag.retrieval.dense import build_dense_index
from constitutional_evidence_rag.retrieval.store import BM25_KIND, DENSE_KIND, index_dir

from corpora_fixtures import A_PAGES, B_PAGES, embedder_factory, make_settings, pdf_bytes


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def service(settings):
    svc = CorpusService(settings, embedder_factory=embedder_factory)
    yield svc
    svc.shutdown()


@pytest.fixture
def two_corpora(service):
    """Corpus A ('zeppelin' only) and corpus B ('marmalade' only), both built and READY."""
    for corpus_id, filename, pages in (("corpus-a", "document_A.pdf", A_PAGES), ("corpus-b", "document_B.pdf", B_PAGES)):
        service.create_corpus(corpus_id)
        service.add_documents(corpus_id, [(filename, pdf_bytes(pages))])
        service.build_corpus(corpus_id)
    return service


@pytest.fixture
def curated(settings):
    """A small curated corpus (the Phase 4 fixture corpus) under the same storage roots."""
    from answer_fixtures import make_chunks, write_corpus
    chunks = make_chunks()
    write_corpus(settings.paths.data_processed_dir, chunks)
    BM25Index.build(chunks).save(index_dir(settings.paths.indexes_dir, CURATED_CORPUS_ID, BM25_KIND))
    build_dense_index(chunks, index_dir(settings.paths.indexes_dir, CURATED_CORPUS_ID, DENSE_KIND), embedder_factory())
    return chunks
