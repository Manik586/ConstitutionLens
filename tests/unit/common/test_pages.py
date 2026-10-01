from datetime import datetime

import pytest
from pydantic import ValidationError

from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType
from constitutional_evidence_rag.common.pages import ParsedDocument, ParsedPage
from constitutional_evidence_rag.common.provenance import provenance_from_document

SHA = "a" * 64


def _document(**overrides) -> DocumentMetadata:
    fields = dict(
        document_id="CONST-INDIA",
        corpus_id="constitutional-core",
        source_type=SourceType.CONSTITUTIONAL_TEXT,
        title="The Constitution of India",
        source_url="https://example.gov/constitution",
        ingestion_date=datetime(2026, 1, 1),
    )
    fields.update(overrides)
    return DocumentMetadata(**fields)


def _page(document: DocumentMetadata, number: int, text: str = "Article text") -> ParsedPage:
    return ParsedPage(
        provenance=provenance_from_document(document, page_number=number),
        text=text,
        is_empty=not text.strip(),
    )


def _parsed(document: DocumentMetadata, pages: list[ParsedPage], **overrides) -> ParsedDocument:
    fields = dict(
        document_id=document.document_id,
        corpus_id="constitutional-core",
        document_version=document.document_version,
        source_type=document.source_type,
        source_file="constitution/coi.pdf",
        file_sha256=SHA,
        page_count=len(pages),
        pages=pages,
    )
    fields.update(overrides)
    return ParsedDocument(**fields)


def test_valid_parsed_document_round_trips_through_json():
    doc = _document()
    parsed = _parsed(doc, [_page(doc, 1), _page(doc, 2, "   ")])

    restored = ParsedDocument.model_validate_json(parsed.model_dump_json())

    assert restored == parsed
    assert restored.empty_page_numbers == [2]


def test_page_requires_a_page_number():
    doc = _document()
    with pytest.raises(ValidationError, match="page_number"):
        ParsedPage(provenance=provenance_from_document(doc), text="x", is_empty=False)


def test_is_empty_must_match_text():
    doc = _document()
    with pytest.raises(ValidationError, match="is_empty"):
        ParsedPage(provenance=provenance_from_document(doc, 1), text="  \n", is_empty=False)


def test_page_from_another_document_is_rejected():
    doc, other = _document(), _document(document_id="JUDG-OTHER", source_type=SourceType.JUDGMENT)
    with pytest.raises(ValidationError, match="provenance points to"):
        _parsed(doc, [_page(doc, 1), _page(other, 2)])


def test_page_from_another_version_is_rejected():
    doc, v2 = _document(), _document(document_version=2)
    with pytest.raises(ValidationError, match="provenance points to"):
        _parsed(doc, [_page(doc, 1), _page(v2, 2)])


@pytest.mark.parametrize("numbers", [[2, 3], [1, 3], [2, 1]])
def test_pages_must_be_numbered_one_to_n_in_order(numbers):
    doc = _document()
    with pytest.raises(ValidationError, match="numbered 1..N"):
        _parsed(doc, [_page(doc, n) for n in numbers])


def test_page_count_must_match_pages():
    doc = _document()
    with pytest.raises(ValidationError, match="page_count"):
        _parsed(doc, [_page(doc, 1)], page_count=2)


def test_zero_page_document_is_rejected():
    with pytest.raises(ValidationError):
        _parsed(_document(), [])


def test_file_hash_must_be_sha256_hex():
    doc = _document()
    with pytest.raises(ValidationError):
        _parsed(doc, [_page(doc, 1)], file_sha256="not-a-hash")


def test_page_from_another_corpus_is_rejected():
    doc, elsewhere = _document(), _document(corpus_id="some-collection")
    with pytest.raises(ValidationError, match="provenance points to"):
        _parsed(doc, [_page(doc, 1), _page(elsewhere, 2)])
