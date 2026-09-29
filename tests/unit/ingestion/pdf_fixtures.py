"""Builders for small PDF fixtures generated locally with PyMuPDF at test time.

Nothing is downloaded. The judgment fixture is synthetic — it is not the
text of any real judgment — so tests never attribute invented content to
a real case.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import yaml

from constitutional_evidence_rag.common.models import DocumentMetadata, SourceType

CONSTITUTION_PAGES = [
    "Article 14. Equality before law.\nThe State shall not deny to any person equality before the law "
    "or the equal protection of the laws within the territory of India.",
    "Article 21. Protection of life and personal liberty.\nNo person shall be deprived of his life or "
    "personal liberty except according to procedure established by law.",
]

JUDGMENT_PAGES = [
    "SYNTHETIC TEST FIXTURE - not a real judgment.\nPetitioner v. Union of India\n"
    "The petitioner submitted that Article 21 was violated.",
    None,  # a page with no extractable text (e.g. a blank or image-only page)
    "The Court held that the procedure must be fair, just and reasonable.",
]

# A syntactically valid PDF whose page tree is empty. PyMuPDF refuses to
# *save* a zero-page document, so this one is written by hand.
ZERO_PAGE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)

FIXED_TIME = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def write_pdf(path: Path, pages: list[str | None], first_page_label: int | None = None, **save_kwargs) -> Path:
    """Write a PDF with one page per item; None produces a page with no text."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text is not None:
            page.insert_textbox(pymupdf.Rect(72, 72, 540, 770), text, fontsize=11)
    if first_page_label is not None:
        doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": first_page_label}])
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, **save_kwargs)
    doc.close()
    return path


def write_manifest(path: Path, entries: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"documents": entries}, sort_keys=False), encoding="utf-8")
    return path


def sample_document(**overrides) -> DocumentMetadata:
    fields = dict(
        document_id="JUDG-000000000001",
        source_type=SourceType.JUDGMENT,
        title="Petitioner v. Union of India (synthetic)",
        source_url="https://example.org/judgments/synthetic-1",
        document_version=1,
        ingestion_date=FIXED_TIME,
    )
    fields.update(overrides)
    return DocumentMetadata(**fields)


CONSTITUTION_ENTRY = {
    "file": "constitution/constitution_of_india.pdf",
    "source_type": "constitutional_text",
    "title": "The Constitution of India",
    "source_url": "https://example.org/constitution-of-india",
}
JUDGMENT_ENTRY = {
    "file": "judgments/synthetic_judgment.pdf",
    "source_type": "judgment",
    "title": "Petitioner v. Union of India (synthetic)",
    "source_url": "https://example.org/judgments/synthetic-1",
    "source_date": "2020-01-15",
}
