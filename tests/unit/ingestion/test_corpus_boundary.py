"""The V1/V2 corpus boundary (SRS v4.2 amendment, D15).

V1 only ever ingests the curated corpus, but the ingestion core is generic
over `corpus_id` so V2 user collections reuse it unchanged. These tests pin
the properties V2 will rely on — above all, that ingesting into any other
corpus can never modify the curated corpus's files (NFR-04).
"""
import pytest
from pydantic import ValidationError

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, SourceType
from constitutional_evidence_rag.ingestion.metadata import DocumentSpec, load_registry
from constitutional_evidence_rag.ingestion.pipeline import (
    DOCUMENTS_FILENAME,
    METADATA_FILENAME,
    IngestionStatus,
    corpus_dir,
    ingest_corpus,
    ingest_document,
    ingest_documents,
    load_parsed_documents,
)

from pdf_fixtures import FIXED_TIME, JUDGMENT_ENTRY, JUDGMENT_PAGES, write_pdf

OTHER = "test-collection"  # stands in for a V2 user collection; no V2 code involved
SPEC = DocumentSpec.model_validate({k: v for k, v in JUDGMENT_ENTRY.items() if k != "file"})


def _snapshot(directory):
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def test_v1_adapter_targets_the_curated_corpus_by_default(corpus):
    ingest_corpus(corpus["manifest"], corpus["raw"], corpus["processed"], ingestion_date=FIXED_TIME)

    assert [p.name for p in corpus["processed"].iterdir()] == [CURATED_CORPUS_ID]
    documents = load_parsed_documents(corpus_dir(corpus["processed"], CURATED_CORPUS_ID) / DOCUMENTS_FILENAME)
    assert {d.corpus_id for d in documents} == {CURATED_CORPUS_ID}
    assert all(p.provenance.corpus_id == CURATED_CORPUS_ID for d in documents for p in d.pages)


def test_ingesting_another_corpus_never_touches_the_curated_corpus(corpus, tmp_path):
    ingest_corpus(corpus["manifest"], corpus["raw"], corpus["processed"], ingestion_date=FIXED_TIME)
    curated = corpus_dir(corpus["processed"], CURATED_CORPUS_ID)
    before = _snapshot(curated)

    # Same judgment PDF, ingested into a different corpus via the generic interface.
    upload = write_pdf(tmp_path / "uploads" / "my_judgment.pdf", JUDGMENT_PAGES)
    result = ingest_document(upload, SPEC, corpus_id=OTHER, processed_root=corpus["processed"],
                             ingestion_date=FIXED_TIME)

    assert result.status is IngestionStatus.INGESTED
    assert _snapshot(curated) == before  # byte-identical: the curated snapshot is untouched
    assert sorted(p.name for p in corpus["processed"].iterdir()) == sorted([CURATED_CORPUS_ID, OTHER])


def test_same_source_in_two_corpora_gets_distinct_ids(corpus, tmp_path):
    ingest_corpus(corpus["manifest"], corpus["raw"], corpus["processed"], ingestion_date=FIXED_TIME)
    upload = write_pdf(tmp_path / "my_judgment.pdf", JUDGMENT_PAGES)
    other = ingest_document(upload, SPEC, corpus_id=OTHER, processed_root=corpus["processed"])

    curated_rows = load_registry(corpus_dir(corpus["processed"], CURATED_CORPUS_ID) / METADATA_FILENAME)
    other_rows = load_registry(corpus_dir(corpus["processed"], OTHER) / METADATA_FILENAME)
    curated_judgment = next(r for r in curated_rows if r.source_type is SourceType.JUDGMENT)

    assert other.document_id != curated_judgment.document_id
    assert [(r.document_id, r.corpus_id, r.document_version) for r in other_rows] == [(other.document_id, OTHER, 1)]


def test_generic_single_document_ingestion_preserves_provenance(tmp_path):
    upload = write_pdf(tmp_path / "in" / "some.pdf", JUDGMENT_PAGES)

    result = ingest_document(upload, SPEC, corpus_id=OTHER, processed_root=tmp_path / "out")

    [document] = load_parsed_documents(corpus_dir(tmp_path / "out", OTHER) / DOCUMENTS_FILENAME)
    assert (result.file, result.page_count, result.empty_pages) == ("some.pdf", 3, (2,))
    assert document.source_file == "some.pdf"
    assert [(p.provenance.corpus_id, p.provenance.document_id, p.provenance.page_number)
            for p in document.pages] == [(OTHER, result.document_id, n) for n in (1, 2, 3)]


def test_generic_ingestion_reports_failures_like_v1(tmp_path):
    result = ingest_document(tmp_path / "missing.pdf", SPEC, corpus_id=OTHER, processed_root=tmp_path / "out")

    assert result.status is IngestionStatus.FAILED and result.error.startswith("MissingPDFError")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("bad", ["", "Upper", "../escape", "a/b", "-lead", "trail-", "has space", "x" * 65])
def test_invalid_corpus_id_is_rejected_before_anything_is_written(tmp_path, bad):
    upload = write_pdf(tmp_path / "doc.pdf", JUDGMENT_PAGES)

    with pytest.raises(ValidationError):
        ingest_document(upload, SPEC, corpus_id=bad, processed_root=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_same_source_twice_in_one_call_is_rejected(tmp_path):
    a = write_pdf(tmp_path / "a.pdf", JUDGMENT_PAGES)
    b = write_pdf(tmp_path / "b.pdf", JUDGMENT_PAGES)

    with pytest.raises(ValueError, match="more than once"):
        ingest_documents([(a, SPEC), (b, SPEC)], corpus_id=OTHER, processed_root=tmp_path / "out",
                         base_dir=tmp_path)
