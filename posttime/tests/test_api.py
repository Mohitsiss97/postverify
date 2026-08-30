"""HTTP contract — har platform ka apna route."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

TWEET = "https://x.com/elonmusk/status/1026872652290379776"


@pytest.fixture()
def client(monkeypatch):
    for var in ("YOUTUBE_API_KEY", "IG_ACCESS_TOKEN", "FB_ACCESS_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return TestClient(app)


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and "x" in body["platforms"]


def test_platform_list_drives_the_picker(client):
    body = client.get("/platforms").json()
    ids = [p["id"] for p in body["platforms"]]
    assert ids == ["x", "linkedin", "youtube", "instagram", "facebook"]
    for p in body["platforms"]:
        assert p["endpoint"] == "/" + p["id"] + "/resolve"
        assert p["sample_url"] and p["how"]
        assert isinstance(p["ready"], bool)


def test_setup_flags(client):
    p = {x["id"]: x for x in client.get("/platforms").json()["platforms"]}

    # offline platforms — kuch chahiye hi nahi
    assert p["x"]["needs_setup"] is False and p["x"]["ready"] is True
    assert p["x"]["setup_env"] is None

    # YouTube — key optional hai, bina uske bhi ready
    assert p["youtube"]["ready"] is True
    assert p["youtube"]["needs_setup"] is False
    assert p["youtube"]["setup_optional"] is True
    assert p["youtube"]["configured"] is False
    assert p["youtube"]["setup_env"] == "YOUTUBE_API_KEY"

    # Instagram — token optional, browser se bhi chal jaata hai
    assert p["instagram"]["setup_optional"] is True
    assert p["instagram"]["needs_setup"] is False

    # Facebook — ab browser se bhi chal jaata hai, token optional
    assert p["facebook"]["setup_optional"] is True
    assert p["facebook"]["needs_setup"] is False


def test_platform_info_endpoint(client):
    body = client.get("/x/info").json()
    assert body["id"] == "x" and body["method"] == "id-embedded"


def test_x_route_resolves(client):
    r = client.post("/x/resolve", json={"url": TWEET, "tz": "Asia/Kolkata"})
    assert r.status_code == 200
    d = r.json()
    assert d["platform"] == "x"
    assert d["post_id"] == "1026872652290379776"
    assert d["published_at"].startswith("2018-08-07T16:48:13")
    assert d["published_at_local"].startswith("2018-08-07T22:18")
    assert d["precision"] == "millisecond"


def test_wrong_platform_route_refuses(client):
    """Instagram ke route pe X ka link — 400, aur batata hai asli platform kaunsa hai."""
    r = client.post("/instagram/resolve", json={"url": TWEET})
    assert r.status_code == 400
    d = r.json()["detail"]
    assert d["error"] == "wrong_platform"
    assert (d["expected"], d["actual"]) == ("instagram", "x")


def test_unsupported_url(client):
    r = client.post("/x/resolve", json={"url": "https://example.com/nope"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsupported_url"


def test_invalid_id(client):
    r = client.post("/x/resolve", json={"url": "https://x.com/jack/status/20"})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "invalid_id"


@pytest.mark.parametrize("pid,url", [
    ("instagram", "https://www.instagram.com/p/CxYzAbC1234/"),
    ("facebook", "https://www.facebook.com/NASA/posts/1615702003258503"),
])
def test_no_browser_is_503(client, monkeypatch, pid, url):
    """Chrome na ho to browser waale platforms saaf batayein, crash na karein."""
    from app.resolvers import browser
    monkeypatch.setattr(browser, "available", lambda: False)
    monkeypatch.setattr(browser, "chrome_path", lambda: None)

    r = client.post("/" + pid + "/resolve", json={"url": url})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "not_configured"


def test_auto_route_still_works(client):
    r = client.post("/resolve", json={"url": TWEET})
    assert r.status_code == 200 and r.json()["platform"] == "x"


def test_batch(client):
    r = client.post("/resolve/batch", json={"urls": [TWEET, "https://example.com/x"]})
    results = r.json()["results"]
    assert results[0]["ok"] is True and results[0]["data"]["platform"] == "x"
    assert results[1]["ok"] is False


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "PostTime" in r.text
