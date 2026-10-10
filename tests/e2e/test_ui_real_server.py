"""Real end-to-end: the browser drives the actual API (no mocks). Test embedder, LLM provider
`none`, PDFs generated at test time. Verifies corpus isolation through the UI."""
import re

from playwright.sync_api import expect

from conftest import SECRET
from corpora_fixtures import A_PAGES, B_PAGES, pdf_bytes


def create_and_index(page, name, filename, pages):
    page.locator("#newCorpusBtn").click()
    page.locator("#newName").fill(name)
    page.locator("#createSubmit").click()
    expect(page.locator("#corpusTitle")).to_have_text(name)
    page.locator("#fileInput").set_input_files([{"name": filename, "mimeType": "application/pdf", "buffer": pdf_bytes(pages)}])
    expect(page.locator(".file-result--accepted")).to_contain_text(filename)
    expect(page.locator("#corpusBadge")).to_contain_text("Not indexed")
    page.locator("#buildBtn").click()
    expect(page.locator("#corpusBadge")).to_contain_text("Ready", timeout=60000)  # real polling
    expect(page.locator("#corpusMeta")).to_contain_text("1 indexed document")


def ask(page, question):
    page.locator("#question").fill(question)
    page.locator("#askBtn").click()
    expect(page.locator("#results")).to_be_visible(timeout=60000)


def test_real_server_create_upload_index_query_isolation_and_delete(page, live_server, page_errors):
    page.goto(live_server.url + "/")
    expect(page.locator('.corpus-item[data-corpus-id="constitutional-core"]')).to_be_visible()

    create_and_index(page, "E2E Zeppelin", "document_A.pdf", A_PAGES)
    create_and_index(page, "E2E Marmalade", "document_B.pdf", B_PAGES)

    # A-only question in A: evidence comes from document_A in corpus e2e-zeppelin.
    page.locator('.corpus-item[data-corpus-id="e2e-zeppelin"]').click()
    ask(page, "How often must every airship be inspected in the zeppelin hangar?")
    expect(page.locator("#evidenceList .evidence").first).to_contain_text("document_A.pdf")
    page.locator("#evidenceList .evidence summary").first.click()
    expect(page.locator("#evidenceList .evidence").first.locator(".evidence__facts")).to_contain_text("e2e-zeppelin")
    expect(page.locator("#answerKind")).to_contain_text("Quoted passages (no LLM)")
    expect(page.locator("#generateWrap")).to_be_hidden()  # provider none: no generation offered

    # Same question in B: nothing from A may appear.
    page.locator('.corpus-item[data-corpus-id="e2e-marmalade"]').click()
    ask(page, "How often must every airship be inspected in the zeppelin hangar?")
    # (Notices may echo the question's own terms; corpus A's documents and text must never appear.)
    text = page.locator("#results").inner_text()
    assert "document_A.pdf" not in text
    for sentence in A_PAGES:
        assert sentence not in text
    assert "airship" not in page.locator("#evidenceList").inner_text().lower()
    for card in page.locator("#evidenceList .evidence").all():
        card.locator("summary").click()
        expect(card.locator(".evidence__facts")).to_contain_text("e2e-marmalade")

    # Uploading to a READY corpus moves it back to "uploaded" (real backend behaviour) and blocks queries.
    page.locator("#fileInput").set_input_files([{"name": "extra.pdf", "mimeType": "application/pdf",
                                                 "buffer": pdf_bytes(["Marmalade labels list the citrus variety."])}])
    expect(page.locator("#corpusBadge")).to_contain_text("Not indexed")
    expect(page.locator("#askBtn")).to_be_disabled()

    # Nothing secret or server-side leaks into the page.
    html = page.content()
    assert SECRET not in html
    assert str(live_server.root) not in html and str(live_server.root).replace("\\", "/") not in html
    assert not re.search(r"[A-Za-z]:\\\\Users|/tmp/pytest", html)

    # Delete both through the confirmation dialog.
    for cid in ("e2e-marmalade", "e2e-zeppelin"):
        page.locator(f'.corpus-item[data-corpus-id="{cid}"]').click()
        page.locator("#deleteBtn").click()
        page.locator("#deleteConfirm").click()
        expect(page.locator(f'.corpus-item[data-corpus-id="{cid}"]')).to_have_count(0)
