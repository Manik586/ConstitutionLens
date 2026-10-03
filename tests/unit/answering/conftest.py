import pytest

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID
from constitutional_evidence_rag.generation.pipeline import AnswerPipeline
from constitutional_evidence_rag.retrieval.bm25 import BM25Index
from constitutional_evidence_rag.retrieval.dense import build_dense_index
from constitutional_evidence_rag.retrieval.store import BM25_KIND, DENSE_KIND, index_dir

from answer_fixtures import HashingEmbedder, make_chunks, make_settings, write_corpus


@pytest.fixture
def chunks():
    return make_chunks()


@pytest.fixture
def built(tmp_path, chunks):
    settings = make_settings(tmp_path)
    write_corpus(settings.paths.data_processed_dir, chunks)
    BM25Index.build(chunks).save(index_dir(settings.paths.indexes_dir, CURATED_CORPUS_ID, BM25_KIND))
    build_dense_index(chunks, index_dir(settings.paths.indexes_dir, CURATED_CORPUS_ID, DENSE_KIND), HashingEmbedder())
    return {"settings": settings, "chunks": chunks, "tmp": tmp_path}


@pytest.fixture
def pipeline(built):
    s = built["settings"]
    return AnswerPipeline.load(processed_root=s.paths.data_processed_dir, indexes_root=s.paths.indexes_dir,
                               corpus_id=CURATED_CORPUS_ID, settings=s, embedder=HashingEmbedder())
