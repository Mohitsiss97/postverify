"""HTTP contract — har platform ka apna route."""
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import fetch
from app import media as reg
from app.main import app
from app.media import ExtractionError, ImageRef
from tests.test_compare import jpg, make_image

TWEET = "https://x.com/NASA/status/1935477485525180417"
POST_IMAGE = jpg(make_image(21))


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def stub(monkeypatch):
    """Post ki image fix kar do — koi network call nahi."""
    async def images(self, match):
        return [ImageRef("https://cdn.example/post.jpg", "post", "post ki main image")]

    async def get_image(url, client=None):
        return POST_IMAGE

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)


def post(client, pid, url, data, filename="a.jpg"):
    return client.post(f"/{pid}/match", data={"url": url},
                       files={"image": (filename, data, "image/jpeg")})


# ---------------- meta ----------------

def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and "x" in body["platforms"]


def test_platform_list_drives_picker(client):
    body = client.get("/platforms").json()
    ids = [p["id"] for p in body["platforms"]]
    assert ids == ["x", "instagram", "facebook", "linkedin", "youtube"]
    for p in body["platforms"]:
        assert p["endpoint"] == f"/{p['id']}/match"
        assert p["sample_url"] and p["how"]


def test_browser_free_platforms_flagged(client):
    p = {x["id"]: x for x in client.get("/platforms").json()["platforms"]}
    assert p["x"]["needs_browser"] is False and p["x"]["ready"] is True
    assert p["youtube"]["needs_browser"] is False
    assert p["instagram"]["needs_browser"] is True


def test_info_endpoint(client):
    assert client.get("/x/info").json()["id"] == "x"


# ---------------- matching ----------------

def test_same_image_is_found(client, stub):
    r = post(client, "x", TWEET, POST_IMAGE)
    assert r.status_code == 200
    d = r.json()
    assert d["present"] is True
    assert d["verdict"] == "identical"
    assert d["matched"]["tier"] == "post"
    assert d["images_checked"] == 1


def test_resized_image_is_found(client, stub):
    small = cv2.resize(make_image(21), (200, 200))
    d = post(client, "x", TWEET, jpg(small)).json()
    assert d["present"] is True and d["verdict"] == "same"


def test_different_image_is_not_found(client, stub):
    d = post(client, "x", TWEET, jpg(make_image(99))).json()
    assert d["present"] is False
    assert d["verdict"] == "different"
    assert d["matched"] is None
    assert "nahi mili" in d["summary"]


def test_carousel_reports_which_one_matched(client, monkeypatch):
    """Post me kai images hain — jo match hui wahi report honi chahiye."""
    slides = {"https://cdn.example/1.jpg": jpg(make_image(31)),
              "https://cdn.example/2.jpg": jpg(make_image(32)),
              "https://cdn.example/3.jpg": jpg(make_image(33))}

    async def images(self, match):
        return [ImageRef(u, "post" if i == 0 else "page", f"slide {i + 1}")
                for i, u in enumerate(slides)]

    async def get_image(url, client=None):
        return slides[url]

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)

    d = post(client, "x", TWEET, jpg(make_image(33))).json()
    assert d["present"] is True
    assert d["matched"]["url"].endswith("3.jpg")
    assert d["images_checked"] == 3


def test_one_broken_image_does_not_kill_the_request(client, monkeypatch):
    async def images(self, match):
        return [ImageRef("https://cdn.example/broken.jpg", "page", "toota"),
                ImageRef("https://cdn.example/ok.jpg", "post", "sahi")]

    async def get_image(url, client=None):
        if "broken" in url:
            raise fetch.FetchError("404")
        return POST_IMAGE

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)

    d = post(client, "x", TWEET, POST_IMAGE).json()
    assert d["present"] is True and d["images_checked"] == 1


# ---------------- errors ----------------

def test_wrong_platform(client):
    r = post(client, "instagram", TWEET, POST_IMAGE)
    assert r.status_code == 400
    d = r.json()["detail"]
    assert d["error"] == "wrong_platform"
    assert (d["expected"], d["actual"]) == ("instagram", "x")


def test_unsupported_url(client):
    r = post(client, "x", "https://example.com/nope", POST_IMAGE)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsupported_url"


def test_junk_upload_rejected(client):
    r = post(client, "x", TWEET, b"ye image nahi hai")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_image"


def test_post_with_no_images(client, monkeypatch):
    async def images(self, match):
        raise ExtractionError("Is tweet me koi image nahi hai",
                              platform="x", reason="no_media")

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    r = post(client, "x", TWEET, POST_IMAGE)
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "no_media"


def test_no_browser_is_503(client, monkeypatch):
    from app import browser
    monkeypatch.setattr(browser, "available", lambda: False)
    monkeypatch.setattr(browser, "chrome_path", lambda: None)
    r = post(client, "instagram", "https://www.instagram.com/p/DceLPdrCR3L/", POST_IMAGE)
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "not_configured"


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "ImageMatch" in r.text
