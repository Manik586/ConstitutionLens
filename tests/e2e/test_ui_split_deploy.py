"""D33 split deployment, end to end: the frontend built by vercel/build.mjs is served from one
origin, the real API (CORS + access token) from another, as with Vercel + a tunnel."""
import functools
import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect

from constitutional_evidence_rag.common.config import ApiSettings

from conftest import LiveServer, _free_port

REPO = Path(__file__).resolve().parents[2]
TOKEN = "split-deploy-token-1234567890"


@pytest.fixture(scope="module")
def split(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to run vercel/build.mjs")
    site_port = _free_port()
    site = f"http://127.0.0.1:{site_port}"
    api = LiveServer(tmp_path_factory.mktemp("split-api"),
                     api=ApiSettings(cors_allowed_origins=[site], access_token=TOKEN))
    api.start()
    out = tmp_path_factory.mktemp("vercel-dist")
    subprocess.run([node, str(REPO / "vercel" / "build.mjs")], check=True, capture_output=True, cwd=REPO,
                   env={**os.environ, "API_BASE": api.url, "OUT_DIR": str(out)})
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *args: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", site_port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"site": site, "api": api}
    httpd.shutdown()
    api.stop()


def test_frontend_on_another_origin_asks_for_the_token_then_works(page, split, page_errors):
    requests = []
    page.on("request", lambda r: requests.append((r.method, r.url, r.headers.get("x-access-token"))))
    page.goto(split["site"] + "/")

    # The API refuses the first calls (no token yet); the UI asks for one.
    dialog = page.locator("#tokenDialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#tokenError")).to_be_hidden()
    page.locator("#tokenInput").fill("not-the-right-token-000")
    page.locator("#tokenSubmit").click()
    expect(dialog).to_be_visible()
    expect(page.locator("#tokenError")).to_have_text("That token was not accepted. Check it and try again.")

    page.locator("#tokenInput").fill(TOKEN)
    page.locator("#tokenSubmit").click()
    expect(dialog).to_be_hidden()
    expect(page.locator('.corpus-item[data-corpus-id="constitutional-core"]')).to_be_visible()

    # Every call went cross-origin to the API, and the authorised ones carried the token.
    api_calls = [r for r in requests if r[1].startswith(split["api"].url)]
    assert api_calls and all(not u.startswith(split["site"] + "/corpora") for _, u, _ in requests)
    assert any(tok == TOKEN for _, _, tok in api_calls)
    assert page.evaluate("() => document.querySelector('meta[name=\"api-base\"]').content") == split["api"].url

    # The token is not stored beyond this tab unless "remember" was ticked, and never shown.
    assert page.evaluate("() => localStorage.getItem('constitutionlens.accessToken')") is None
    assert TOKEN not in page.content()


def test_the_built_page_allows_only_its_api_origin(page, split):
    page.goto(split["site"] + "/")
    csp = page.evaluate("() => document.querySelector('meta[http-equiv=\"Content-Security-Policy\"]').content")
    assert f"connect-src 'self' {split['api'].url}" in csp and "unsafe-inline" not in csp
