"""D33: a frontend on another origin (CORS) and the optional shared access token."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from constitutional_evidence_rag.api.app import create_app  # noqa: E402
from constitutional_evidence_rag.common.config import ApiSettings, ConfigError, load_settings  # noqa: E402
from constitutional_evidence_rag.corpora.service import CorpusService  # noqa: E402

from corpora_fixtures import embedder_factory, make_settings  # noqa: E402

SITE = "https://lens-demo.vercel.app"
TOKEN = "t0ken-for-tests-1234567890"


def client(tmp_path, **api):
    settings = make_settings(tmp_path).model_copy(update={"api": ApiSettings(**api)})
    service = CorpusService(settings, embedder_factory=embedder_factory)
    return TestClient(create_app(settings, service), raise_server_exceptions=False)


def test_defaults_are_same_origin_only_and_open(tmp_path):
    with client(tmp_path) as c:
        r = c.get("/corpora", headers={"Origin": SITE})
        assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
        pre = c.options("/corpora", headers={"Origin": SITE, "Access-Control-Request-Method": "GET"})
        assert "access-control-allow-origin" not in pre.headers


def test_listed_origin_may_call_the_api_and_others_may_not(tmp_path):
    with client(tmp_path, cors_allowed_origins=[SITE]) as c:
        r = c.get("/corpora", headers={"Origin": SITE})
        assert r.headers["access-control-allow-origin"] == SITE
        assert "access-control-allow-credentials" not in r.headers  # no cookies, ever
        pre = c.options("/corpora/x/query", headers={"Origin": SITE, "Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Headers": "content-type,x-access-token,ngrok-skip-browser-warning"})
        assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == SITE
        allowed = pre.headers["access-control-allow-headers"].lower()
        assert "x-access-token" in allowed and "ngrok-skip-browser-warning" in allowed
        other = c.get("/corpora", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in other.headers


def test_token_protects_every_api_route_except_health_and_the_ui(tmp_path):
    with client(tmp_path, cors_allowed_origins=[SITE], access_token=TOKEN) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/").status_code == 200 and c.get("/static/app.css").status_code == 200
        for method, path in [("get", "/config"), ("get", "/corpora"), ("get", "/corpora/constitutional-core"),
                             ("delete", "/corpora/x"), ("post", "/corpora/x/build")]:
            r = getattr(c, method)(path, headers={"Origin": SITE})
            assert r.status_code == 401, path
            assert r.json() == {"error": "unauthorized", "message": "This server requires an access token."}
            assert r.headers["access-control-allow-origin"] == SITE  # the browser can read the 401
        assert c.get("/corpora", headers={"X-Access-Token": "wrong-token-000000000"}).status_code == 401
        ok = c.get("/corpora", headers={"X-Access-Token": TOKEN})
        assert ok.status_code == 200 and ok.json()["corpora"]
        assert TOKEN not in c.get("/config", headers={"X-Access-Token": TOKEN}).text
        # preflights are answered without the token (browsers never send it on OPTIONS)
        pre = c.options("/corpora", headers={"Origin": SITE, "Access-Control-Request-Method": "GET",
                                             "Access-Control-Request-Headers": "x-access-token"})
        assert pre.status_code == 200


def test_settings_reject_wildcards_and_short_tokens(monkeypatch):
    with pytest.raises(ValueError):
        ApiSettings(cors_allowed_origins=["*"])
    with pytest.raises(ValueError):
        ApiSettings(cors_allowed_origins=["https://site.example/path"])
    with pytest.raises(ValueError):
        ApiSettings(access_token="short")
    assert ApiSettings(cors_allowed_origins=[" https://a.example/ "]).cors_allowed_origins == ["https://a.example"]
    assert TOKEN not in repr(ApiSettings(access_token=TOKEN))


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{SITE}, http://localhost:5173")
    monkeypatch.setenv("API_ACCESS_TOKEN", TOKEN)
    api = load_settings().api
    assert api.cors_allowed_origins == [SITE, "http://localhost:5173"] and api.access_token == TOKEN
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ConfigError):
        load_settings()
