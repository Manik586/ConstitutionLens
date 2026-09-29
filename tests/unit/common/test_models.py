from datetime import datetime

import pytest
from pydantic import ValidationError

from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType


def test_document_metadata_valid_construction():
    doc = DocumentMetadata(
        document_id="CONST-INDIA",
        source_type=SourceType.CONSTITUTIONAL_TEXT,
        title="The Constitution of India",
        source_url="https://example.gov/constitution",
        ingestion_date=datetime(2026, 1, 1),
    )

    assert doc.document_version == 1
    assert doc.replaced_by is None
    assert doc.source_date is None
    assert doc.source_type == SourceType.CONSTITUTIONAL_TEXT


def test_document_metadata_rejects_unknown_source_type():
    with pytest.raises(ValidationError):
        DocumentMetadata(
            document_id="X",
            source_type="not_a_real_type",
            title="X",
            source_url="https://example.com",
            ingestion_date=datetime(2026, 1, 1),
        )


def test_document_metadata_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        DocumentMetadata(
            document_id="X",
            source_type=SourceType.JUDGMENT,
            title="X",
            source_url="https://example.com",
            ingestion_date=datetime(2026, 1, 1),
            unexpected_field="should fail",
        )


def test_document_metadata_supports_versioning_fields():
    doc = DocumentMetadata(
        document_id="DOC-002",
        source_type=SourceType.JUDGMENT,
        title="Some Judgment (revised OCR pass)",
        source_url="https://example.gov/judgments/some-judgment",
        document_version=2,
        replaced_by=None,
        ingestion_date=datetime(2026, 2, 1),
    )

    assert doc.document_version == 2


@pytest.mark.parametrize(
    "override",
    [{"document_version": 0}, {"document_id": ""}, {"title": ""}, {"source_url": ""}],
)
def test_document_metadata_rejects_invalid_values(override):
    fields = dict(
        document_id="X",
        source_type=SourceType.JUDGMENT,
        title="X",
        source_url="https://example.com",
        ingestion_date=datetime(2026, 1, 1),
    )
    fields.update(override)
    with pytest.raises(ValidationError):
        DocumentMetadata(**fields)
