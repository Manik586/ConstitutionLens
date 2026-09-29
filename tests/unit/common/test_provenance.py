from datetime import datetime

import pytest
from pydantic import ValidationError

from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType
from constitutional_evidence_rag.common.provenance import provenance_from_document


def _sample_document() -> DocumentMetadata:
    return DocumentMetadata(
        document_id="DOC-KESAVANANDA-1973",
        source_type=SourceType.JUDGMENT,
        title="Kesavananda Bharati v. State of Kerala",
        source_url="https://example.gov/judgments/kesavananda",
        document_version=2,
        ingestion_date=datetime(2026, 1, 1),
    )


def test_provenance_from_document_copies_expected_fields():
    doc = _sample_document()

    prov = provenance_from_document(doc, page_number=42)

    assert prov.document_id == doc.document_id
    assert prov.source_type == doc.source_type
    assert prov.document_version == doc.document_version
    assert prov.source_url == doc.source_url
    assert prov.page_number == 42


def test_provenance_page_number_defaults_to_none():
    doc = _sample_document()

    prov = provenance_from_document(doc)

    assert prov.page_number is None


def test_provenance_page_numbers_are_one_based():
    with pytest.raises(ValidationError):
        provenance_from_document(_sample_document(), page_number=0)
