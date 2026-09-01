"""Integration API — /api/v1/*"""
import cv2
import pytest
from fastapi.testclient import TestClient

from app import fetch
from app import platforms as reg
from app.main import app
from app.platforms import ImageRef, PlatformError
from app.store import store
from tests.test_compare import jpg, make_image

# 7 Aug 2018 ka tweet — hamesha purana rahega, to window checks pakke hain
OLD_TWEET = "https://x.com/elonmusk/status/1026872652290379776"
TWEET = "https://x.com/NASA/status/1935477485525180417"
POST_IMAGE = jpg(make_image(21))


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_store():
    yield
    for token in list(store._sessions):
        store.drop(token)


@pytest.fixture()
def stub_images(monkeypatch):
    async def images(self, match, ctx):
        return [ImageRef("https://cdn.example/post.jpg", "post", "post ki main image")]

    async def get_image(url, client=None):
        return POST_IMAGE

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)


# ---------------- time ----------------

def test_post_time_shape(client):
    d = client.post("/api/v1/time", json={"url": OLD_TWEET, "tz": "Asia/Kolkata"}).json()
    assert d["ok"] is True
    assert d["platform"] == "x"
    assert d["post_id"] == "1026872652290379776"
    assert d["canonical_url"].startswith("https://x.com/")
    t = d["time"]
    assert t["published_at"] == "2018-08-07T16:48:13.334000Z"
    assert t["published_at_local"].startswith("2018-08-07T22:18")
    assert t["timezone"] == "Asia/Kolkata"
    assert t["method"] == "id-embedded"
    assert t["precision"] == "millisecond"
    assert t["age_seconds"] > 0


def test_get_works_too(client):
    d = client.get("/api/v1/time", params={"url": OLD_TWEET}).json()
    assert d["ok"] is True and d["time"]["published_at"].startswith("2018-08-07")


def test_time_only_downloads_nothing(client, monkeypatch):
    """Sirf time chahiye to image download nahi honi chahiye."""
    async def boom(*a, **k):
        raise AssertionError("time-only call me image download nahi honi chahiye")

    monkeypatch.setattr(fetch, "get_image", boom)
    assert client.post("/api/v1/time", json={"url": OLD_TWEET}).status_code == 200


def test_nothing_left_behind(client):
    before = store.stats()["sessions"]
    client.post("/api/v1/time", json={"url": OLD_TWEET})
    assert store.stats()["sessions"] == before


# ---------------- within ----------------

def test_within_on_an_old_post(client):
    d = client.post("/api/v1/time",
                    json={"url": OLD_TWEET, "within": "1d,3d,7d,1m"}).json()
    assert d["within"] == {"1d": False, "3d": False, "7d": False, "1m": False}


def test_within_wide_enough_window_is_true(client):
    d = client.post("/api/v1/time", json={"url": OLD_TWEET, "within": "20y"}).json()
    assert d["within"]["20y"] is True


def test_single_window_gives_a_plain_boolean(client):
    """Ek hi window ho to is_within seedha mil jaata hai — integration aasan."""
    d = client.post("/api/v1/time", json={"url": OLD_TWEET, "within": "7d"}).json()
    assert d["is_within"] is False
    assert d["within"] == {"7d": False}


def test_many_windows_have_no_plain_boolean(client):
    d = client.post("/api/v1/time", json={"url": OLD_TWEET, "within": "7d,20y"}).json()
    assert "is_within" not in d
    assert d["within"] == {"7d": False, "20y": True}


def test_within_detail_and_checked_at(client):
    d = client.post("/api/v1/time", json={"url": OLD_TWEET, "within": "7d"}).json()
    assert d["within_detail"]["7d"]["seconds"] == 7 * 86_400
    assert d["within_detail"]["7d"]["cutoff"].endswith("Z")
    assert d["checked_at"].endswith("Z")


def test_within_labels_come_back_as_written(client):
    d = client.post("/api/v1/time", json={"url": OLD_TWEET, "within": "1w, 30d"}).json()
    assert set(d["within"]) == {"1w", "30d"}


def test_no_within_no_key(client):
    d = client.post("/api/v1/time", json={"url": OLD_TWEET}).json()
    assert "within" not in d and "is_within" not in d


def test_bad_window_is_a_clear_400(client):
    r = client.post("/api/v1/time", json={"url": OLD_TWEET, "within": "1 hafta"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_window"


def test_window_checked_before_any_work(client, monkeypatch):
    """Galat window pe network chhune ka koi matlab nahi."""
    async def boom(*a, **k):
        raise AssertionError("bad window pe kaam shuru nahi hona chahiye")

    monkeypatch.setattr(fetch, "get_html", boom)
    r = client.post("/api/v1/time", json={"url": OLD_TWEET, "within": "kachra"})
    assert r.status_code == 400


def test_within_when_time_is_missing(client, monkeypatch):
    """Time hi na mile to window ka jawab dena jhooth hoga."""
    async def no_time(self, match, ctx):
        raise PlatformError("time nahi nikla", platform="x", reason="upstream_error")

    async def images(self, match, ctx):
        return [ImageRef("https://cdn.example/post.jpg", "post", "img")]

    async def get_image(url, client=None):
        return POST_IMAGE

    monkeypatch.setattr(reg.get("x").__class__, "published_at", no_time)
    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)

    r = client.post("/api/v1/verify", data={"url": TWEET, "within": "7d"},
                    files={"image": ("a.jpg", POST_IMAGE, "image/jpeg")})
    d = r.json()
    assert d["within"] is None
    assert "within_error" in d
    assert d["image"]["present"] is True, "image match phir bhi chalna chahiye"


# ---------------- verify ----------------

def test_verify_returns_time_and_match(client, stub_images):
    r = client.post("/api/v1/verify", data={"url": TWEET, "tz": "Asia/Kolkata"},
                    files={"image": ("a.jpg", POST_IMAGE, "image/jpeg")})
    d = r.json()
    assert d["ok"] is True
    assert d["time"]["published_at"]
    img = d["image"]
    assert img["checked"] is True
    assert img["present"] is True
    assert img["verdict"] == "identical"
    assert img["score"] == 100
    assert img["matched"]["tier"] == "post"
    assert img["images_checked"] == 1


def test_verify_with_within(client, stub_images):
    r = client.post("/api/v1/verify", data={"url": TWEET, "within": "1d"},
                    files={"image": ("a.jpg", POST_IMAGE, "image/jpeg")})
    d = r.json()
    assert "is_within" in d and d["image"]["present"] is True


def test_verify_resized_image(client, stub_images):
    small = jpg(cv2.resize(make_image(21), (200, 200)))
    r = client.post("/api/v1/verify", data={"url": TWEET},
                    files={"image": ("a.jpg", small, "image/jpeg")})
    d = r.json()["image"]
    assert d["present"] is True and d["score"] >= 70


def test_verify_different_image(client, stub_images):
    r = client.post("/api/v1/verify", data={"url": TWEET},
                    files={"image": ("a.jpg", jpg(make_image(99)), "image/jpeg")})
    d = r.json()["image"]
    assert d["present"] is False and d["verdict"] == "different"
    assert "matched" not in d


def test_verify_accepts_an_image_url(client, monkeypatch):
    """Server-to-server ke liye file upload ki jagah URL bhi chalta hai."""
    async def images(self, match, ctx):
        return [ImageRef("https://cdn.example/post.jpg", "post", "img")]

    async def get_image(url, client=None):
        return POST_IMAGE          # dono taraf wahi image

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)

    r = client.post("/api/v1/verify",
                    data={"url": TWEET, "image_url": "https://cdn.example/meri.jpg"})
    assert r.json()["image"]["present"] is True


def test_verify_needs_an_image(client):
    r = client.post("/api/v1/verify", data={"url": TWEET})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_image"


def test_verify_cleans_up(client, stub_images):
    before = store.stats()["sessions"]
    client.post("/api/v1/verify", data={"url": TWEET},
                files={"image": ("a.jpg", POST_IMAGE, "image/jpeg")})
    assert store.stats()["sessions"] == before
    assert store.stats()["files"] == 0


# ---------------- errors ----------------

def test_unsupported_url(client):
    r = client.post("/api/v1/time", json={"url": "https://example.com/x"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsupported_url"


def test_pre_snowflake_tweet(client):
    r = client.post("/api/v1/time", json={"url": "https://twitter.com/jack/status/20"})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "invalid_id"


def test_junk_image(client, stub_images):
    r = client.post("/api/v1/verify", data={"url": TWEET},
                    files={"image": ("a.jpg", b"ye image nahi hai", "image/jpeg")})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_image"


# ---------------- access token ----------------

def test_api_is_open_without_token(client):
    assert client.post("/api/v1/time", json={"url": OLD_TWEET}).status_code == 200


def test_api_locked_when_token_set(client, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "khulja-sim-sim")
    assert client.post("/api/v1/time", json={"url": OLD_TWEET}).status_code == 401
    assert client.get("/api/v1/time", params={"url": OLD_TWEET}).status_code == 401


def test_api_token_in_body(client, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "khulja-sim-sim")
    r = client.post("/api/v1/time",
                    json={"url": OLD_TWEET, "token": "khulja-sim-sim"})
    assert r.status_code == 200


def test_api_token_as_header(client, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "khulja-sim-sim")
    r = client.post("/api/v1/time", json={"url": OLD_TWEET},
                    headers={"X-Access-Token": "khulja-sim-sim"})
    assert r.status_code == 200


def test_api_token_as_query(client, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "khulja-sim-sim")
    r = client.get("/api/v1/time",
                   params={"url": OLD_TWEET, "token": "khulja-sim-sim"})
    assert r.status_code == 200


def test_api_wrong_token(client, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "khulja-sim-sim")
    r = client.post("/api/v1/time", json={"url": OLD_TWEET, "token": "galat"})
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "unauthorized"
