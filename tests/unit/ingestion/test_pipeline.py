import json
from datetime import datetime, timezone

import pytest

from constitutional_evidence_rag.common.models import CURATED_CORPUS_ID, SourceType
from constitutional_evidence_rag.ingestion.metadata import ManifestError, load_registry, make_document_id
from constitutional_evidence_rag.ingestion.pipeline import (
    DOCUMENTS_FILENAME,
    METADATA_FILENAME,
    IngestionStatus,
    corpus_dir,
    ingest_corpus,
    load_parsed_documents,
)

from pdf_fixtures import (
    CONSTITUTION_ENTRY,
    CONSTITUTION_PAGES,
    FIXED_TIME,
    JUDGMENT_ENTRY,
    write_manifest,
    write_pdf,
)

JUDGMENT_ID = make_document_id(CURATED_CORPUS_ID, SourceType.JUDGMENT, JUDGMENT_ENTRY["source_url"])
CONSTITUTION_ID = make_document_id(CURATED_CORPUS_ID, SourceType.CONSTITUTIONAL_TEXT, CONSTITUTION_ENTRY["source_url"])


def run(corpus, **kwargs):
    kwargs.setdefault("ingestion_date", FIXED_TIME)
    return ingest_corpus(corpus["manifest"], corpus["raw"], corpus["processed"], **kwargs)


def core_dir(corpus):
    return corpus_dir(corpus["processed"], CURATED_CORPUS_ID)


def outputs(corpus):
    target = core_dir(corpus)
    return load_parsed_documents(target / DOCUMENTS_FILENAME), load_registry(target / METADATA_FILENAME)


def test_ingests_all_documents_and_writes_both_files(corpus):
    report = run(corpus)

    assert [r.status for r in report.results] == [IngestionStatus.INGESTED] * 2
    assert not report.failed
    documents, registry = outputs(corpus)
    assert {d.document_id for d in documents} == {CONSTITUTION_ID, JUDGMENT_ID}
    assert {(r.document_id, r.document_version) for r in registry} == {(CONSTITUTION_ID, 1), (JUDGMENT_ID, 1)}


def test_every_page_traces_back_to_a_registered_source_document(corpus):
    """The core invariant: text -> page -> source document, end to end."""
    run(corpus)
    documents, registry = outputs(corpus)
    by_key = {(r.document_id, r.document_version): r for r in registry}

    for document in documents:
        for number, page in enumerate(document.pages, start=1):
            prov = page.provenance
            source = by_key[(prov.document_id, prov.document_version)]
            assert prov.page_number == number
            assert prov.source_url == source.source_url
            assert prov.source_type == source.source_type


def test_registry_metadata_comes_from_manifest(corpus):
    run(corpus)
    _, registry = outputs(corpus)
    judgment = next(r for r in registry if r.document_id == JUDGMENT_ID)

    assert judgment.title == JUDGMENT_ENTRY["title"]
    assert judgment.source_url == JUDGMENT_ENTRY["source_url"]
    assert str(judgment.source_date) == JUDGMENT_ENTRY["source_date"]
    assert judgment.ingestion_date == FIXED_TIME
    assert judgment.replaced_by is None


def test_page_level_details_are_preserved(corpus):
    report = run(corpus)
    documents, _ = outputs(corpus)
    judgment = next(d for d in documents if d.document_id == JUDGMENT_ID)

    assert judgment.source_file == JUDGMENT_ENTRY["file"]
    assert judgment.page_count == 3
    assert judgment.empty_page_numbers == [2]
    assert [p.page_label for p in judgment.pages] == ["225", "226", "227"]
    assert "procedure must be fair" in judgment.pages[2].text
    assert next(r for r in report.results if r.document_id == JUDGMENT_ID).empty_pages == (2,)


def test_rerun_with_unchanged_inputs_is_a_no_op(corpus):
    run(corpus)
    documents_file = core_dir(corpus) / DOCUMENTS_FILENAME
    metadata_file = core_dir(corpus) / METADATA_FILENAME
    before = (documents_file.read_bytes(), metadata_file.read_bytes())

    report = run(corpus, ingestion_date=datetime(2027, 1, 1, tzinfo=timezone.utc))

    assert [r.status for r in report.results] == [IngestionStatus.UNCHANGED] * 2
    assert (documents_file.read_bytes(), metadata_file.read_bytes()) == before


def test_changed_pdf_creates_new_version_with_same_id(corpus):
    run(corpus)
    write_pdf(corpus["raw"] / JUDGMENT_ENTRY["file"], ["Revised OCR pass of the synthetic judgment."])

    report = run(corpus, ingestion_date=datetime(2026, 10, 1, tzinfo=timezone.utc))

    statuses = {r.document_id: (r.status, r.document_version) for r in report.results}
    assert statuses[JUDGMENT_ID] == (IngestionStatus.INGESTED, 2)
    assert statuses[CONSTITUTION_ID] == (IngestionStatus.UNCHANGED, 1)

    documents, registry = outputs(corpus)
    versions = {r.document_version: r for r in registry if r.document_id == JUDGMENT_ID}
    assert versions[1].replaced_by == f"{JUDGMENT_ID}@v2"
    assert versions[2].replaced_by is None

    judgment_docs = {d.document_version: d for d in documents if d.document_id == JUDGMENT_ID}
    assert set(judgment_docs) == {1, 2}  # old version retained (NFR-07, UC-6)
    assert judgment_docs[1].page_count == 3 and judgment_docs[2].page_count == 1
    assert all(p.provenance.document_version == 2 for p in judgment_docs[2].pages)


def test_metadata_change_on_same_file_creates_new_version(corpus):
    run(corpus)
    corrected = {**JUDGMENT_ENTRY, "title": "Petitioner v. Union of India (synthetic, corrected title)"}
    write_manifest(corpus["manifest"], [CONSTITUTION_ENTRY, corrected])

    report = run(corpus)

    judgment = next(r for r in report.results if r.document_id == JUDGMENT_ID)
    assert (judgment.status, judgment.document_version) == (IngestionStatus.INGESTED, 2)


def test_one_bad_document_does_not_block_the_others(corpus):
    bad_entries = [
        {**JUDGMENT_ENTRY, "file": "judgments/missing.pdf", "source_url": "https://example.org/j/missing"},
        {**JUDGMENT_ENTRY, "file": "judgments/scanned.pdf", "source_url": "https://example.org/j/scanned"},
        {**JUDGMENT_ENTRY, "file": "judgments/garbage.pdf", "source_url": "https://example.org/j/garbage"},
    ]
    write_pdf(corpus["raw"] / "judgments/scanned.pdf", [None, None])
    (corpus["raw"] / "judgments/garbage.pdf").write_bytes(b"not a pdf")
    write_manifest(corpus["manifest"], [CONSTITUTION_ENTRY, *bad_entries, JUDGMENT_ENTRY])

    report = run(corpus)

    assert len(report.ingested) == 2
    errors = {r.file: r.error for r in report.failed}
    assert errors["judgments/missing.pdf"].startswith("MissingPDFError")
    assert errors["judgments/scanned.pdf"].startswith("NoExtractableTextError")
    assert errors["judgments/garbage.pdf"].startswith("UnreadablePDFError")

    documents, registry = outputs(corpus)
    assert len(documents) == len(registry) == 2  # failed documents leave no trace in the output


def test_all_failures_write_nothing(corpus):
    write_manifest(corpus["manifest"], [{**JUDGMENT_ENTRY, "file": "judgments/missing.pdf"}])

    report = run(corpus)

    assert len(report.failed) == 1
    assert not corpus["processed"].exists()


def test_orphaned_document_rows_are_dropped_on_next_write(corpus):
    run(corpus)
    documents_file = core_dir(corpus) / DOCUMENTS_FILENAME
    orphan = json.loads(documents_file.read_text().splitlines()[0])
    orphan["document_version"] = 99
    for page in orphan["pages"]:
        page["provenance"]["document_version"] = 99
    with documents_file.open("a") as f:
        f.write(json.dumps(orphan) + "\n")
    write_pdf(corpus["raw"] / CONSTITUTION_ENTRY["file"], CONSTITUTION_PAGES + ["Article 21A. Right to education."])

    run(corpus)

    documents, registry = outputs(corpus)
    assert all(d.document_version != 99 for d in documents)
    assert {(d.document_id, d.document_version) for d in documents} == {
        (r.document_id, r.document_version) for r in registry
    }


def test_invalid_manifest_raises_before_writing(corpus):
    corpus["manifest"].write_text("documents: [{file: x.pdf}]\n")

    with pytest.raises(ManifestError):
        run(corpus)
    assert not corpus["processed"].exists()
