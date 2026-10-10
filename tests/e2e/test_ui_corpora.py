"""Phase 8a: corpus list, create, upload, build, status polling, constitutional-core rules."""
from playwright.sync_api import expect

from conftest import advance, corpus

PDF = b"%PDF-1.4\n% test\n"


def item(page, cid):
    return page.locator(f'.corpus-item[data-corpus-id="{cid}"]')


def test_corpus_list_renders_api_data_and_statuses_are_not_hard_coded(page, mock_api, open_app):
    mock_api.corpora = [
        corpus("constitutional-core", "Curated corpus", "uploaded", 0, 0, reserved=True),  # NOT ready per the API
        corpus("alpha", "Alpha leases", "processing", 2, 0),
        corpus("beta", "Beta notes", "failed", 1, 0, error="No extractable text in 'scan.pdf'."),
        corpus("gamma", "Gamma memos", "ready", 3, 41),
    ]
    open_app()
    expect(page.locator(".corpus-item")).to_have_count(4)
    expect(item(page, "gamma")).to_contain_text("Gamma memos")
    expect(item(page, "gamma")).to_contain_text("Ready")
    expect(item(page, "gamma")).to_contain_text("3 indexed documents · 41 passages")
    expect(item(page, "alpha")).to_contain_text("Processing")
    expect(item(page, "beta")).to_contain_text("Failed")
    expect(item(page, "constitutional-core")).to_contain_text("Not indexed")
    expect(item(page, "constitutional-core")).to_contain_text("Built-in · query only")
    # The curated corpus is selected by default, but the API says it is not ready: no querying.
    expect(page.locator("#corpusTitle")).to_have_text("Curated corpus")
    expect(page.locator("#askBtn")).to_be_disabled()
    item(page, "beta").click()
    expect(page.locator("#corpusError")).to_have_text("Indexing failed: No extractable text in 'scan.pdf'.")
    # Status badges carry text, not just colour.
    expect(page.locator("#corpusBadge")).to_have_text("✕Failed")


def test_create_corpus_sends_the_right_body_and_refetches(page, mock_api, open_app):
    open_app()
    lists_before = mock_api.count("GET", "/corpora")
    page.locator("#newCorpusBtn").click()
    page.locator("#newName").fill("Tenancy Briefs 2026")
    expect(page.locator("#newId")).to_have_value("tenancy-briefs-2026")
    page.locator("#createSubmit").click()
    expect(item(page, "tenancy-briefs-2026")).to_be_visible()
    [create] = mock_api.find("POST", "/corpora")
    assert create["json"] == {"corpus_id": "tenancy-briefs-2026", "name": "Tenancy Briefs 2026"}
    assert mock_api.count("GET", "/corpora") > lists_before  # re-fetched, not assumed
    expect(page.locator("#corpusTitle")).to_have_text("Tenancy Briefs 2026")
    expect(page.locator("#uploadSection")).to_be_visible()


def test_create_corpus_shows_the_server_validation_error(page, mock_api, open_app):
    mock_api.create_response = (409, {"error": "corpus_exists", "message": "Corpus 'briefs' already exists."})
    open_app()
    page.locator("#newCorpusBtn").click()
    page.locator("#newName").fill("Briefs")
    page.locator("#createSubmit").click()
    expect(page.locator("#createError")).to_have_text("Corpus 'briefs' already exists.")
    expect(page.locator("#createForm")).to_be_visible()


def test_upload_is_multipart_with_files_field_and_shows_per_file_results(page, mock_api, open_app):
    mock_api.upload_response = (200, {
        "corpus": corpus("briefs", "Case briefs", "uploaded", 3, 12),
        "files": [
            {"filename": "lease.pdf", "status": "accepted", "reason": None, "pages": 2, "size_bytes": 900, "stored_as": "a.pdf"},
            {"filename": "scan.pdf", "status": "rejected", "reason": "The PDF has no extractable text (scanned images need OCR, which is not supported).",
             "pages": None, "size_bytes": 800, "stored_as": None},
        ]})
    open_app("briefs")
    gets_before = mock_api.count("GET", "/corpora/briefs")
    page.locator("#fileInput").set_input_files([
        {"name": "lease.pdf", "mimeType": "application/pdf", "buffer": PDF},
        {"name": "scan.pdf", "mimeType": "application/pdf", "buffer": PDF},
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"plain text"},
    ])
    rows = page.locator("#fileResults .file-result")
    expect(rows).to_have_count(3)
    expect(page.locator(".file-result--accepted")).to_contain_text("lease.pdf")
    expect(page.locator(".file-result--accepted")).to_contain_text("2 pages")
    expect(page.locator(".file-result--rejected").first).to_contain_text("no extractable text")
    expect(page.locator(".file-result--rejected", has_text="notes.txt")).to_contain_text("only .pdf files are accepted")

    [upload] = mock_api.find("POST", "/corpora/briefs/documents")
    assert upload["headers"]["content-type"].startswith("multipart/form-data; boundary=")
    raw = upload["raw"].decode("latin-1")
    assert raw.count('name="files"; filename="lease.pdf"') == 1 and raw.count('name="files"; filename="scan.pdf"') == 1
    assert "notes.txt" not in raw  # the convenience pre-check kept it out; the server stays authoritative
    expect(page.locator("#corpusBadge")).to_contain_text("Not indexed")  # status re-fetched after the upload
    assert mock_api.count("GET", "/corpora/briefs") > gets_before
    expect(page.locator("#askBtn")).to_be_disabled()
    expect(page.locator("#askDisabledMsg")).to_contain_text("not indexed yet")


def test_upload_failure_is_visible_and_the_ui_stays_usable(page, mock_api, open_app):
    mock_api.upload_response = (500, {"error": "internal_error", "message": "Internal error; the details are in the server log."})
    open_app("briefs")
    page.locator("#fileInput").set_input_files([{"name": "a.pdf", "mimeType": "application/pdf", "buffer": PDF}])
    expect(page.locator("#uploadError")).to_have_text("Upload failed: Internal error; the details are in the server log.")
    expect(page.locator("#fileResults .file-result")).to_have_count(0)
    item(page, "constitutional-core").click()
    expect(page.locator("#corpusTitle")).to_contain_text("Constitution of India")


def test_build_starts_polling_and_status_goes_processing_to_ready(page, mock_api, open_app):
    mock_api.corpora.append(corpus("leases", "Leases", "uploaded", 2, 0))
    ready = corpus("leases", "Leases", "ready", 2, 9)
    processing = corpus("leases", "Leases", "processing", 2, 0)
    open_app("leases", clock=True)
    expect(page.locator("#buildBtn")).to_be_enabled()
    mock_api.corpus_script["leases"] = [processing, processing, "network", processing, ready]
    page.locator("#buildBtn").click()
    expect(page.locator("#statusCard")).to_be_visible()
    expect(page.locator("#statusText")).to_have_text("Processing documents…")
    assert mock_api.count("POST", "/corpora/leases/build") == 1

    advance(page, 2100)  # poll 1 (2 s cadence): still processing
    expect(page.locator("#corpusBadge")).to_contain_text("Processing")
    advance(page, 2100)  # poll 2: transient network failure
    expect(page.locator("#statusSub")).to_contain_text("Reconnecting…")
    advance(page, 2100)  # retry after backoff: processing again
    expect(page.locator("#statusSub")).to_contain_text("checks the status automatically")
    advance(page, 2100)  # READY
    expect(page.locator("#corpusBadge")).to_contain_text("Ready")
    expect(page.locator("#statusCard")).to_be_hidden()
    expect(page.locator("#askBtn")).to_be_enabled()
    polls = mock_api.count("GET", "/corpora/leases")
    advance(page, 30000)
    assert mock_api.count("GET", "/corpora/leases") == polls  # stopped on READY


def test_polling_backs_off_after_thirty_seconds_and_stops_on_failed(page, mock_api, open_app):
    mock_api.corpora.append(corpus("slow", "Slow corpus", "processing", 1, 0))
    open_app(clock=True)
    item(page, "slow").click()
    mock_api.corpus_script["slow"] = [corpus("slow", "Slow corpus", "processing", 1, 0)] * 20
    advance(page, 30500)  # fast phase: one poll every 2 s
    fast = mock_api.count("GET", "/corpora/slow")
    assert 14 <= fast <= 16, fast
    advance(page, 20000)  # slow phase: one poll every 5 s
    assert mock_api.count("GET", "/corpora/slow") - fast in (3, 4, 5)
    mock_api.corpus_script["slow"] = [corpus("slow", "Slow corpus", "failed", 1, 0, error="Indexing failed: no text.")]
    advance(page, 5100)
    expect(page.locator("#corpusError")).to_contain_text("Indexing failed")
    polls = mock_api.count("GET", "/corpora/slow")
    advance(page, 30000)
    assert mock_api.count("GET", "/corpora/slow") == polls


def test_polling_stops_when_switching_corpus(page, mock_api, open_app):
    mock_api.corpora.append(corpus("proc", "Processing corpus", "processing", 1, 0))
    open_app(clock=True)
    item(page, "proc").click()
    mock_api.corpus_script["proc"] = [corpus("proc", "Processing corpus", "processing", 1, 0)] * 50
    advance(page, 2100)
    expect(page.locator("#statusCard")).to_be_visible()
    item(page, "briefs").click()
    polls = mock_api.count("GET", "/corpora/proc")
    advance(page, 60000)
    assert mock_api.count("GET", "/corpora/proc") == polls


def test_polling_gives_up_with_a_clear_message_after_repeated_network_failures(page, mock_api, open_app):
    mock_api.corpora.append(corpus("proc", "Processing corpus", "processing", 1, 0))
    open_app(clock=True)
    item(page, "proc").click()
    mock_api.corpus_script["proc"] = ["network"] * 10
    advance(page, 60000)
    expect(page.locator("#corpusError")).to_contain_text("Lost contact with the server")
    assert mock_api.count("GET", "/corpora/proc") == 5


def test_constitutional_core_is_query_only(page, mock_api, open_app):
    open_app("constitutional-core")
    expect(page.locator("#uploadSection")).to_be_hidden()
    expect(page.locator("#buildBtn")).to_be_hidden()
    expect(page.locator("#deleteBtn")).to_be_hidden()
    expect(page.locator("#fileInput")).to_be_hidden()
    expect(page.locator("#corpusMeta")).to_contain_text("Built-in corpus · query only")
    expect(page.locator("#askBtn")).to_be_enabled()  # READY comes from the API


def test_delete_requires_confirmation_and_refetches(page, mock_api, open_app):
    open_app("briefs")
    page.locator("#deleteBtn").click()
    dialog = page.locator("#deleteDialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#deleteName")).to_have_text("Case briefs")
    expect(page.locator("#deleteCancel")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page.locator("#deleteBtn")).to_be_focused()  # focus returns to the trigger
    assert mock_api.count("DELETE", "/corpora/briefs") == 0

    page.locator("#deleteBtn").click()
    page.locator("#deleteConfirm").click()
    expect(item(page, "briefs")).to_have_count(0)
    assert mock_api.count("DELETE", "/corpora/briefs") == 1
    expect(page.locator("#corpusTitle")).to_have_text("No corpus selected")


def test_failed_delete_is_reported_and_nothing_is_removed(page, mock_api, open_app):
    mock_api.delete_response = (409, {"error": "corpus_busy", "message": "Corpus 'briefs' is still processing; try again when the build finishes."})
    open_app("briefs")
    page.locator("#deleteBtn").click()
    page.locator("#deleteConfirm").click()
    expect(page.locator("#corpusError")).to_contain_text("Could not delete the corpus: Corpus 'briefs' is still processing")
    expect(item(page, "briefs")).to_have_count(1)


def test_polling_pauses_while_the_page_is_hidden_and_resumes_when_visible(page, mock_api, open_app):
    mock_api.corpora.append(corpus("proc", "Processing corpus", "processing", 1, 0))
    open_app(clock=True)
    item(page, "proc").click()
    mock_api.corpus_script["proc"] = [corpus("proc", "Processing corpus", "processing", 1, 0)] * 50
    advance(page, 2100)
    page.evaluate("""() => {
        Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
        document.dispatchEvent(new Event("visibilitychange"));
    }""")
    polls = mock_api.count("GET", "/corpora/proc")
    advance(page, 20000)
    assert mock_api.count("GET", "/corpora/proc") == polls  # no requests while hidden
    mock_api.corpus_script["proc"] = [corpus("proc", "Processing corpus", "ready", 1, 4)]
    page.evaluate("""() => {
        Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
        document.dispatchEvent(new Event("visibilitychange"));
    }""")
    advance(page, 500)
    expect(page.locator("#corpusBadge")).to_contain_text("Ready")


def test_build_of_an_empty_corpus_shows_the_api_refusal(page, mock_api, open_app):
    mock_api.corpora.append(corpus("empty", "Empty corpus", "uploaded", 0, 0))
    mock_api.build_response = (422, {"error": "corpus_build_failed", "message": "Corpus 'empty' has no documents; upload PDFs first."})
    open_app("empty")
    expect(page.locator("#buildBtn")).to_be_enabled()  # document_count counts indexed documents only
    page.locator("#buildBtn").click()
    expect(page.locator("#corpusError")).to_have_text("Could not start indexing: Corpus 'empty' has no documents; upload PDFs first.")
    expect(page.locator("#statusCard")).to_be_hidden()
