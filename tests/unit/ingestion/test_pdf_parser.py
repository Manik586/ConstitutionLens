import logging

import pymupdf
import pytest

from constitutional_evidence_rag.ingestion.pdf_parser import (
    EmptyPDFError,
    MissingPDFError,
    NoExtractableTextError,
    UnreadablePDFError,
    parse_pdf,
)

from pdf_fixtures import CONSTITUTION_PAGES, JUDGMENT_PAGES, ZERO_PAGE_PDF, sample_document, write_pdf


def test_extracts_one_record_per_page_in_order(tmp_path):
    pdf = write_pdf(tmp_path / "c.pdf", CONSTITUTION_PAGES)

    pages = parse_pdf(pdf, sample_document())

    assert [p.provenance.page_number for p in pages] == [1, 2]
    assert "Article 14" in pages[0].text and "Article 21" not in pages[0].text
    assert "Article 21" in pages[1].text and "Article 14" not in pages[1].text


def test_every_page_carries_document_provenance(tmp_path):
    pdf = write_pdf(tmp_path / "j.pdf", JUDGMENT_PAGES)
    document = sample_document(document_version=3)

    pages = parse_pdf(pdf, document)

    for page in pages:
        prov = page.provenance
        assert prov.document_id == document.document_id
        assert prov.document_version == 3
        assert prov.source_type == document.source_type
        assert prov.source_url == document.source_url


def test_page_without_text_is_kept_and_flagged(tmp_path, caplog):
    pdf = write_pdf(tmp_path / "j.pdf", JUDGMENT_PAGES)

    with caplog.at_level(logging.WARNING):
        pages = parse_pdf(pdf, sample_document())

    assert len(pages) == 3  # the blank page is not dropped; numbering does not shift
    assert [p.is_empty for p in pages] == [False, True, False]
    assert pages[1].text.strip() == ""
    assert pages[2].provenance.page_number == 3
    assert "No extractable text" in caplog.text and "page 2" in caplog.text


def test_printed_page_labels_are_captured_when_defined(tmp_path):
    labelled = write_pdf(tmp_path / "l.pdf", JUDGMENT_PAGES, first_page_label=225)
    plain = write_pdf(tmp_path / "p.pdf", CONSTITUTION_PAGES)

    assert [p.page_label for p in parse_pdf(labelled, sample_document())] == ["225", "226", "227"]
    assert [p.page_label for p in parse_pdf(plain, sample_document())] == [None, None]


def test_typographic_ligatures_are_expanded(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_htmlbox(pymupdf.Rect(50, 50, 500, 200), "<p>signi\ufb01cant \ufb02ow</p>")
    path = tmp_path / "lig.pdf"
    doc.save(path)

    [page] = parse_pdf(path, sample_document())

    assert "significant flow" in page.text
    assert "\ufb01" not in page.text and "\ufb02" not in page.text


def test_missing_pdf(tmp_path):
    with pytest.raises(MissingPDFError):
        parse_pdf(tmp_path / "nope.pdf", sample_document())


def test_directory_instead_of_pdf_is_missing(tmp_path):
    with pytest.raises(MissingPDFError):
        parse_pdf(tmp_path, sample_document())


def test_non_pdf_bytes_are_unreadable(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"this is not a pdf")

    with pytest.raises(UnreadablePDFError):
        parse_pdf(path, sample_document())


def test_password_protected_pdf_is_unreadable(tmp_path):
    path = write_pdf(
        tmp_path / "enc.pdf", CONSTITUTION_PAGES,
        encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner",
    )

    with pytest.raises(UnreadablePDFError, match="password"):
        parse_pdf(path, sample_document())


def test_zero_page_pdf_is_empty(tmp_path):
    path = tmp_path / "zero.pdf"
    path.write_bytes(ZERO_PAGE_PDF)

    with pytest.raises(EmptyPDFError):
        parse_pdf(path, sample_document())


def test_pdf_with_no_text_on_any_page(tmp_path):
    path = write_pdf(tmp_path / "scanned.pdf", [None, None])

    with pytest.raises(NoExtractableTextError):
        parse_pdf(path, sample_document())


def test_single_page_extraction_failure_is_isolated(tmp_path, monkeypatch):
    pdf = write_pdf(tmp_path / "c.pdf", CONSTITUTION_PAGES)
    original = pymupdf.Page.get_text

    def flaky_get_text(self, *args, **kwargs):
        if self.number == 1:
            raise RuntimeError("corrupt content stream")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", flaky_get_text)

    pages = parse_pdf(pdf, sample_document())

    assert pages[0].extraction_error is None and not pages[0].is_empty
    assert pages[1].is_empty and "corrupt content stream" in pages[1].extraction_error
    assert pages[1].provenance.page_number == 2


def test_other_format_renamed_to_pdf_is_unreadable(tmp_path):
    # PyMuPDF opens a zip of images even with filetype="pdf"; found on a
    # real "PDF" that was actually a zip archive of page images.
    import zipfile

    path = tmp_path / "actually_a_zip.pdf"
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False).tobytes("png")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("1.png", image)

    with pytest.raises(UnreadablePDFError, match="not a PDF"):
        parse_pdf(path, sample_document())


def test_page_label_failure_does_not_discard_text(tmp_path, monkeypatch):
    pdf = write_pdf(tmp_path / "c.pdf", CONSTITUTION_PAGES)

    def broken_label(self):
        raise AssertionError("malformed /PageLabels")

    monkeypatch.setattr(pymupdf.Page, "get_label", broken_label)

    pages = parse_pdf(pdf, sample_document())

    assert all(p.page_label is None for p in pages)
    assert all(p.extraction_error is None and not p.is_empty for p in pages)
    assert "Article 21" in pages[1].text
