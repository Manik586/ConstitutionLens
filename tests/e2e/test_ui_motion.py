"""GSAP motion runs under the strict CSP and never leaves content hidden (motion enabled here)."""
from playwright.sync_api import expect

from answers import generated_answer
from conftest import MockApi, corpus, default_config


def test_animations_run_under_csp_and_leave_everything_visible(browser, live_server):
    context = browser.new_context(reduced_motion="no-preference", viewport={"width": 1200, "height": 900})
    page = context.new_page()
    problems = []
    page.on("pageerror", lambda exc: problems.append(str(exc)))
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    api = MockApi(default_config(generation=True), [corpus("briefs", "Case briefs", "ready", 3, 12)])
    api.query_responses = [(200, generated_answer())]
    api.install(page, live_server.url)
    try:
        page.goto(live_server.url + "/")
        assert page.evaluate("() => typeof window.gsap") == "object"  # vendored, same-origin
        page.locator("#question").fill("When is the deposit refundable?")
        page.locator("#askBtn").click()
        expect(page.locator("#results")).to_be_visible()
        page.wait_for_timeout(2500)  # let every intro / reveal tween finish
        for selector in (".brand__name", ".corpus-item", "#answerPanel", "#evidencePanel", "#checkPanel", ".claim"):
            opacity = page.locator(selector).first.evaluate("e => getComputedStyle(e).opacity")
            assert float(opacity) == 1.0, (selector, opacity)
        page.locator("#answerPanel .cite").first.click()
        expect(page.locator("#evidence-E1")).to_be_focused()
        assert not problems, problems
    finally:
        context.close()
