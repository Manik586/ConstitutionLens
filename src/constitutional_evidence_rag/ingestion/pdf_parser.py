"""Page-by-page PDF text extraction (SRS Section 21.1, FR-02 — text and
page numbers; structural fields are a separate, later step, see D8).

This is the only module that imports PyMuPDF, so swapping the parser
(SRS Section 39 allows "a better-suited legal-document parser") means
replacing this file, not touching the pipeline.

Failure policy (docs/DECISIONS.md D12):
    * missing file                  -> MissingPDFError
    * not a PDF (incl. other formats
      renamed .pdf) / corrupt / encrypted -> UnreadablePDFError
    * zero pages                    -> EmptyPDFError
    * pages, but none with any text -> NoExtractableTextError (scanned/image-
                                       only PDF; OCR is out of Phase 1 scope)
    * some pages with no text       -> NOT an error: the page is kept with
                                       is_empty=True and a warning is logged
    * one page fails to extract     -> NOT fatal: kept as an empty page with
                                       extraction_error set
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from constitutional_evidence_rag.common.logging import get_logger
from constitutional_evidence_rag.common.models import DocumentMetadata
from constitutional_evidence_rag.common.pages import ParsedPage
from constitutional_evidence_rag.common.provenance import provenance_from_document

logger = get_logger(__name__)

# PyMuPDF's default "text" flags keep typographic ligatures as single
# glyphs ("signiﬁcant"), which would silently break exact-term matching
# for BM25 later (FR-06). Expanding them yields the plain letters the
# ligature stands for; no other normalization is applied, so the stored
# text stays as close to the source as possible for citation (FR-12).
_TEXT_FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES


class PDFIngestionError(Exception):
    """Base class for a PDF that cannot be ingested at all."""


class MissingPDFError(PDFIngestionError):
    """The PDF path does not exist or is not a file."""


class UnreadablePDFError(PDFIngestionError):
    """The file exists but cannot be opened as a (non-encrypted) PDF."""


class EmptyPDFError(PDFIngestionError):
    """The PDF opened but contains zero pages."""


class NoExtractableTextError(PDFIngestionError):
    """Every page of the PDF yielded no text (typically a scanned PDF)."""


def parse_pdf(path: Path, document: DocumentMetadata) -> list[ParsedPage]:
    """Extract one `ParsedPage` per physical page of `path`, in order.

    Every returned page carries provenance derived from `document`
    (document_id, source_type, version, source_url) plus its 1-based
    page number, so each page is traceable on its own.
    """
    if not path.is_file():
        raise MissingPDFError(f"PDF not found: {path}")

    try:
        pdf = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError, OSError) as exc:
        raise UnreadablePDFError(f"Cannot open {path} as a PDF: {exc}") from exc

    with pdf:
        # `filetype="pdf"` is only a hint: PyMuPDF will still open other
        # formats it recognizes (e.g. a zip of page images renamed .pdf),
        # and PDF-only calls then fail on every page. Check explicitly.
        if not pdf.is_pdf:
            detected = (pdf.metadata or {}).get("format") or "unknown"
            raise UnreadablePDFError(f"{path} is not a PDF (detected format: {detected})")
        if pdf.needs_pass:
            raise UnreadablePDFError(f"PDF is password-protected: {path}")
        if pdf.page_count == 0:
            raise EmptyPDFError(f"PDF has no pages: {path}")

        pages = [_extract_page(pdf, index, document, path) for index in range(pdf.page_count)]

    if all(page.is_empty for page in pages):
        raise NoExtractableTextError(
            f"No extractable text on any of {len(pages)} page(s) of {path} "
            "(likely a scanned/image-only PDF; OCR is not part of Phase 1)"
        )

    empty = [p.provenance.page_number for p in pages if p.is_empty]
    logger.info(
        "Parsed %s: %d page(s), %d without text", document.document_id, len(pages), len(empty)
    )
    return pages


def _extract_page(
    pdf: pymupdf.Document, index: int, document: DocumentMetadata, path: Path
) -> ParsedPage:
    page_number = index + 1
    text = ""
    label: str | None = None
    error: str | None = None

    try:
        page = pdf.load_page(index)
        text = page.get_text("text", flags=_TEXT_FLAGS)
    except Exception as exc:  # noqa: BLE001 — one bad page must not sink the document
        error = f"{type(exc).__name__}: {exc}"
        text = ""
        logger.warning("Text extraction failed on %s page %d: %s", path, page_number, error)
    else:
        # Kept separate so a malformed /PageLabels tree can never discard
        # text that was extracted successfully; the label is optional.
        try:
            label = page.get_label() or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("No usable page label on %s page %d: %s", path, page_number, exc)

    is_empty = not text.strip()
    if is_empty and error is None:
        logger.warning("No extractable text on %s page %d", path, page_number)

    return ParsedPage(
        provenance=provenance_from_document(document, page_number=page_number),
        text=text,
        is_empty=is_empty,
        page_label=label,
        extraction_error=error,
    )
