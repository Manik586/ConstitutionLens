import pytest

from constitutional_evidence_rag.common.config import UploadSettings
from constitutional_evidence_rag.corpora.uploads import SCANNED_REASON, UploadStore, sanitize_filename

from corpora_fixtures import A_PAGES, B_PAGES, encrypted_pdf_bytes, pdf_bytes, scanned_pdf_bytes

GOOD = pdf_bytes(A_PAGES)


def add(tmp_path, files, **limits):
    store = UploadStore(tmp_path / "uploads" / "c")
    return store, store.add(files, UploadSettings(**limits))


def test_single_and_multiple_pdfs(tmp_path):
    store, results = add(tmp_path, [("a.pdf", GOOD), ("b.pdf", pdf_bytes(B_PAGES))])
    assert [(r.status, r.pages) for r in results] == [("accepted", 2), ("accepted", 2)]
    assert sorted(p.name for p in store.directory.glob("*.pdf")) == sorted(r.stored_as for r in results)
    assert {e["title"] for e in store.entries().values()} == {"a.pdf", "b.pdf"}


@pytest.mark.parametrize("name,data,reason", [
    ("notes.txt", GOOD, "Only .pdf files"),
    ("fake.pdf", b"Hello, I am not a PDF", "does not start with %PDF-"),
    ("empty.pdf", b"", "empty"),
    ("corrupt.pdf", b"%PDF-1.7\n" + b"\x00garbage" * 50, "not a readable PDF"),
    ("scan.pdf", scanned_pdf_bytes(), SCANNED_REASON),
    ("locked.pdf", encrypted_pdf_bytes(), "password-protected"),
])
def test_rejections(tmp_path, name, data, reason):
    store, (result,) = add(tmp_path, [(name, data)])
    assert result.status == "rejected" and reason in result.reason
    assert not list(store.directory.glob("*.pdf"))


def test_size_count_total_and_page_limits(tmp_path):
    assert add(tmp_path / "1", [("big.pdf", GOOD)], max_file_size_mb=0.0005)[1][0].reason.startswith("The file is larger than")
    _, results = add(tmp_path / "2", [("a.pdf", GOOD), ("b.pdf", pdf_bytes(B_PAGES))], max_documents_per_corpus=1)
    assert [r.status for r in results] == ["accepted", "rejected"] and "maximum of 1 documents" in results[1].reason
    _, results = add(tmp_path / "3", [("a.pdf", GOOD), ("b.pdf", pdf_bytes(B_PAGES))], max_total_size_mb=len(GOOD) * 1.5 / 1024 / 1024)
    assert [r.status for r in results] == ["accepted", "rejected"] and "corpus limit" in results[1].reason
    _, (result,) = add(tmp_path / "4", [("long.pdf", GOOD)], max_pages_per_document=1)
    assert result.status == "rejected" and "2 pages; the limit is 1" in result.reason


def test_one_bad_file_does_not_abort_the_others(tmp_path):
    _, results = add(tmp_path, [("a.pdf", GOOD), ("bad.pdf", b"nope"), ("b.pdf", pdf_bytes(B_PAGES))])
    assert [r.status for r in results] == ["accepted", "rejected", "accepted"]


def test_identical_reupload_is_unchanged(tmp_path):
    store, _ = add(tmp_path, [("a.pdf", GOOD)])
    results = store.add([("renamed.pdf", GOOD)], UploadSettings())
    assert results[0].status == "unchanged" and len(store.entries()) == 1


def test_hostile_filename_never_becomes_a_path(tmp_path):
    store, (result,) = add(tmp_path, [("../../x.pdf", GOOD)])
    assert result.status == "accepted" and result.filename == "x.pdf"
    assert (store.directory / result.stored_as).exists() and result.stored_as.endswith(".pdf") and "/" not in result.stored_as
    assert not (tmp_path / "x.pdf").exists() and not (tmp_path / "uploads" / "x.pdf").exists()


@pytest.mark.parametrize("raw,clean", [("../../x.pdf", "x.pdf"), ("C:\\\\Users\\\\me\\\\paper.pdf", "paper.pdf"),
                                       ("a<b>:c?.pdf", "abc.pdf"), ("", "document.pdf"), (None, "document.pdf"),
                                       ("...", "document.pdf"), ("résumé.pdf", "résumé.pdf")])
def test_sanitize_filename(raw, clean):
    assert sanitize_filename(raw) == clean
