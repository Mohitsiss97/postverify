"""The HTTP contract of the three endpoints."""
import cv2
import pytest
from fastapi.testclient import TestClient

from app import fetch
from app import platforms as reg
from app.main import app
from app.platforms import ImageRef, PlatformError
from tests.test_compare import jpg, make_image

# A 2018 tweet. It only gets older, so window assertions against it stay stable.
OLD_TWEET = "https://x.com/elonmusk/status/1026872652290379776"
TWEET = "https://x.com/NASA/status/1935477485525180417"
POST_IMAGE = jpg(make_image(21))


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def stub_images(monkeypatch):
    """One image on the post, with no browser and no real download."""
    async def images(self, match, ctx):
        return [ImageRef("https://cdn.example/post.jpg", "post", "post image")]

    async def get_image(url, client=None):
        return POST_IMAGE

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)


def verify(client, image=None, *, url=TWEET, **extra):
    data = {"url": url, **extra}
    files = {"image": ("a.jpg", image, "image/jpeg")} if image is not None else None
    return client.post("/v1/verify", data=data, files=files)


# ---------------- meta ----------------

def test_health(client):
    d = client.get("/health").json()
    assert d["status"] == "ok"
    assert "x" in d["platforms"]
    assert d["locked"] is False
    assert d["version"]


def test_ready_reports_browser_availability(client, monkeypatch):
    from app import browser
    monkeypatch.setattr(browser, "available", lambda: True)
    assert client.get("/ready").json()["status"] == "ready"


def test_ready_is_503_when_a_needed_browser_is_missing(client, monkeypatch):
    """A load balancer should stop routing here, but nothing should restart it.

    Restarting the process will not install Chrome, which is why this is
    separate from /health.
    """
    from app import browser
    monkeypatch.setattr(browser, "available", lambda: False)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert client.get("/health").status_code == 200, "liveness must stay green"


def test_platforms(client):
    d = client.get("/platforms").json()["platforms"]
    assert [p["id"] for p in d] == ["x", "instagram", "facebook", "linkedin", "youtube"]
    x = next(p for p in d if p["id"] == "x")
    assert "x.com" in x["hosts"] and x["needs_browser"] is False


def test_root_points_at_the_docs(client):
    d = client.get("/").json()
    assert d["docs"] == "/docs"
    assert "POST /v1/time" in d["endpoints"]


def test_there_is_no_web_page(client):
    """This is an API-only service: no UI, no session and no media route."""
    for path in ("/prepare", "/session/abc", "/media/abc/0.jpg"):
        assert client.get(path).status_code == 404


# ---------------- 1. time ----------------

def test_time_post(client):
    d = client.post("/v1/time", json={"url": OLD_TWEET, "tz": "Asia/Kolkata"}).json()
    assert d["ok"] is True
    assert d["platform"] == "x"
    assert d["post_id"] == "1026872652290379776"
    t = d["time"]
    assert t["published_at"] == "2018-08-07T16:48:13.334000Z"
    assert t["published_at_local"].startswith("2018-08-07T22:18")
    assert t["timezone"] == "Asia/Kolkata"
    assert t["method"] == "id-embedded"
    assert t["precision"] == "millisecond"
    assert t["age_seconds"] > 0 and t["age_human"]


def test_time_get(client):
    d = client.get("/v1/time", params={"url": OLD_TWEET}).json()
    assert d["time"]["published_at"].startswith("2018-08-07")


def test_time_downloads_no_images(client, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("/v1/time must not download any image")

    monkeypatch.setattr(fetch, "get_image", boom)
    assert client.post("/v1/time", json={"url": OLD_TWEET}).status_code == 200


def test_time_has_no_window_keys(client):
    """/v1/time answers exactly one question."""
    d = client.post("/v1/time", json={"url": OLD_TWEET}).json()
    assert "within" not in d and "image" not in d


def test_bad_timezone_does_not_break_the_answer(client):
    d = client.post("/v1/time", json={"url": OLD_TWEET, "tz": "Mars/Olympus"}).json()
    assert d["time"]["published_at"]
    assert d["time"]["published_at_local"] is None


# ---------------- 2. within ----------------

def test_within_old_post(client):
    d = client.post("/v1/within",
                    json={"url": OLD_TWEET, "within": "1d,3d,7d,15d,1m"}).json()
    assert d["within"] == {"1d": False, "3d": False, "7d": False,
                           "15d": False, "1m": False}


def test_within_wide_window(client):
    d = client.post("/v1/within", json={"url": OLD_TWEET, "within": "20y"}).json()
    assert d["within"]["20y"] is True


def test_single_window_gives_plain_boolean(client):
    d = client.post("/v1/within", json={"url": OLD_TWEET, "within": "7d"}).json()
    assert d["is_within"] is False and d["within"] == {"7d": False}


def test_many_windows_have_no_plain_boolean(client):
    d = client.post("/v1/within", json={"url": OLD_TWEET, "within": "7d,20y"}).json()
    assert "is_within" not in d


def test_within_labels_come_back_as_written(client):
    d = client.post("/v1/within", json={"url": OLD_TWEET, "within": "1w, 15d"}).json()
    assert set(d["within"]) == {"1w", "15d"}


def test_within_detail_shows_the_maths(client):
    d = client.post("/v1/within", json={"url": OLD_TWEET, "within": "15d"}).json()
    assert d["within_detail"]["15d"]["seconds"] == 15 * 86_400
    assert d["within_detail"]["15d"]["cutoff"].endswith("Z")
    assert d["checked_at"].endswith("Z")


def test_within_also_returns_the_time(client):
    d = client.post("/v1/within", json={"url": OLD_TWEET, "within": "7d"}).json()
    assert d["time"]["published_at"].startswith("2018-08-07")


def test_within_get(client):
    d = client.get("/v1/within",
                   params={"url": OLD_TWEET, "within": "1d,20y"}).json()
    assert d["within"] == {"1d": False, "20y": True}


def test_within_is_required(client):
    assert client.post("/v1/within", json={"url": OLD_TWEET}).status_code == 422


def test_bad_window_is_400(client):
    r = client.post("/v1/within", json={"url": OLD_TWEET, "within": "1 fortnight"})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_window"


def test_window_parsed_before_any_work(client, monkeypatch):
    """There is no point starting a 15-second render for a malformed window."""
    async def boom(*a, **k):
        raise AssertionError("no work should start on a bad window")

    monkeypatch.setattr(fetch, "get_html", boom)
    r = client.post("/v1/within", json={"url": OLD_TWEET, "within": "nonsense"})
    assert r.status_code == 400


def test_month_is_not_minute(client):
    """`m` is month and `min` is minute. The confusion is easy, hence the test."""
    month = client.post("/v1/within", json={"url": OLD_TWEET, "within": "1m"}).json()
    minute = client.post("/v1/within", json={"url": OLD_TWEET, "within": "1min"}).json()
    assert month["within_detail"]["1m"]["seconds"] == 30 * 86_400
    assert minute["within_detail"]["1min"]["seconds"] == 60


# ---------------- 3. verify ----------------

def test_verify_finds_the_same_image(client, stub_images):
    d = verify(client, POST_IMAGE, tz="Asia/Kolkata").json()
    assert d["ok"] is True
    assert d["time"]["published_at"]
    img = d["image"]
    assert img["checked"] is True
    assert img["present"] is True
    assert img["verdict"] == "identical"
    assert img["score"] == 100
    assert img["images_checked"] == 1
    assert img["matched"]["tier"] == "post"


def test_verify_resized_image(client, stub_images):
    small = jpg(cv2.resize(make_image(21), (200, 200)))
    img = verify(client, small).json()["image"]
    assert img["present"] is True and img["score"] >= 70


def test_verify_cropped_image(client, stub_images):
    base = make_image(21)
    h, w = base.shape
    crop = jpg(base[int(h * .2):int(h * .8), int(w * .2):int(w * .8)])
    img = verify(client, crop).json()["image"]
    assert img["present"] is True


def test_verify_different_image(client, stub_images):
    img = verify(client, jpg(make_image(99))).json()["image"]
    assert img["present"] is False
    assert img["verdict"] == "different"
    assert "matched" not in img


def test_verify_picks_the_best_of_many(client, monkeypatch):
    slides = {f"https://cdn.example/{i}.jpg": jpg(make_image(30 + i)) for i in range(1, 4)}

    async def images(self, match, ctx):
        return [ImageRef(u, "post" if i == 0 else "page", "")
                for i, u in enumerate(slides)]

    async def get_image(url, client=None):
        return slides[url]

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)

    d = verify(client, jpg(make_image(33))).json()["image"]
    assert d["present"] is True and d["images_checked"] == 3


def test_verify_accepts_an_image_url(client, monkeypatch):
    async def images(self, match, ctx):
        return [ImageRef("https://cdn.example/post.jpg", "post", "")]

    async def get_image(url, client=None):
        return POST_IMAGE          # the same image on both sides

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)

    d = verify(client, image_url="https://cdn.example/mine.jpg").json()
    assert d["image"]["present"] is True


def test_verify_can_do_the_window_too(client, stub_images):
    """So that Instagram does not have to be rendered a second time."""
    d = verify(client, POST_IMAGE, within="20y").json()
    assert d["is_within"] is True
    assert d["image"]["present"] is True


def test_verify_needs_an_image(client):
    r = verify(client)
    assert r.status_code == 400
    assert r.json()["error"] == "bad_image"


def test_verify_rejects_junk(client, stub_images):
    r = verify(client, b"this is not an image")
    assert r.status_code == 400
    assert r.json()["error"] == "bad_image"


def test_junk_upload_checked_before_downloading(client, monkeypatch):
    """A corrupt upload must not cost a download of the post's images."""
    async def boom(*a, **k):
        raise AssertionError("nothing should download for a corrupt upload")

    monkeypatch.setattr(fetch, "get_image", boom)
    assert verify(client, b"garbage").status_code == 400


# ---------------- partial answers ----------------

def test_no_image_on_the_post_still_gives_time(client, monkeypatch):
    async def no_images(self, match, ctx):
        raise PlatformError("This tweet carries no image",
                            platform="x", reason="no_media")

    monkeypatch.setattr(reg.get("x").__class__, "images", no_images)
    d = verify(client, POST_IMAGE).json()
    assert d["time"] is not None
    assert d["image"]["checked"] is False and d["image"]["error"]


def test_within_when_time_is_missing(client, monkeypatch, stub_images):
    """Without a time the window answer is null, not false — false would lie."""
    async def no_time(self, match, ctx):
        raise PlatformError("no timestamp", platform="x", reason="upstream_error")

    monkeypatch.setattr(reg.get("x").__class__, "published_at", no_time)
    d = verify(client, POST_IMAGE, within="7d").json()
    assert d["within"] is None and d["within_error"]
    assert d["image"]["present"] is True, "the image match must still run"


def test_everything_failing_keeps_the_real_reason(client):
    """A pre-2010 tweet must surface invalid_id, not a generic 502."""
    r = client.post("/v1/time", json={"url": "https://twitter.com/jack/status/20"})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_id"


# ---------------- errors ----------------

def test_unsupported_url(client):
    r = client.post("/v1/time", json={"url": "https://example.com/x"})
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_url"


def test_no_browser_is_503(client, monkeypatch):
    from app import browser
    monkeypatch.setattr(browser, "available", lambda: False)
    monkeypatch.setattr(browser, "chrome_path", lambda: None)
    r = client.post("/v1/time", json={"url": "https://www.instagram.com/p/DceLPdrCR3L/"})
    assert r.status_code == 503
    assert r.json()["error"] == "not_configured"


def test_every_error_has_the_same_shape(client):
    """A caller should write one error parser, not one per framework layer.

    The three cases below deliberately take three different routes out: our own
    HTTPException, Pydantic validation, and the framework's own 404.
    """
    cases = [
        client.post("/v1/time", json={"url": "https://example.com/x"}),   # ours
        client.post("/v1/time", json={}),                                 # validation
        client.get("/v1/no-such-route"),                                  # framework
    ]
    for r in cases:
        body = r.json()
        assert r.status_code >= 400
        assert "detail" not in body, f"{r.url} is still wrapped in detail"
        assert isinstance(body.get("error"), str) and body["error"]
        assert isinstance(body.get("message"), str) and body["message"]


def test_validation_error_names_the_fields(client):
    body = client.post("/v1/time", json={}).json()
    assert body["error"] == "invalid_request"
    assert isinstance(body["fields"], list) and body["fields"]
    assert body["fields"][0]["field"] == "url"


def test_unknown_route_is_named_not_found(client):
    r = client.get("/v1/anything")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


# ---------------- request correlation ----------------

def test_every_response_carries_a_request_id(client):
    r = client.get("/health")
    assert r.headers["X-Request-ID"]


def test_an_incoming_request_id_is_kept(client):
    """A trace started by the caller must continue here, not restart."""
    r = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"


def test_security_headers_are_present(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


# ---------------- rate limiting ----------------

def test_rate_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    fresh = TestClient(app)
    # The middleware holds its counters on the app object, which is shared, so
    # this starts from whatever earlier tests left behind. Driving well past the
    # limit makes the assertion independent of that history.
    codes = [fresh.post("/v1/time", json={"url": OLD_TWEET}).status_code
             for _ in range(8)]
    assert 429 in codes
    limited = fresh.post("/v1/time", json={"url": OLD_TWEET})
    assert limited.json()["error"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1


def test_rate_limit_never_throttles_health(monkeypatch):
    """Health checks must keep working while a caller is being limited."""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    fresh = TestClient(app)
    for _ in range(5):
        fresh.post("/v1/time", json={"url": OLD_TWEET})
    assert fresh.get("/health").status_code == 200


# ---------------- access token ----------------

def test_open_by_default(client):
    assert client.post("/v1/time", json={"url": OLD_TWEET}).status_code == 200


def test_all_three_lock_together(client, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "open-sesame")
    assert client.get("/health").json()["locked"] is True
    assert client.post("/v1/time", json={"url": OLD_TWEET}).status_code == 401
    assert client.post("/v1/within",
                       json={"url": OLD_TWEET, "within": "7d"}).status_code == 401
    assert verify(client, POST_IMAGE).status_code == 401


@pytest.mark.parametrize("how", ["body", "header", "query"])
def test_right_token_gets_through(client, monkeypatch, how):
    monkeypatch.setenv("ACCESS_TOKEN", "open-sesame")
    if how == "body":
        r = client.post("/v1/time", json={"url": OLD_TWEET, "token": "open-sesame"})
    elif how == "header":
        r = client.post("/v1/time", json={"url": OLD_TWEET},
                        headers={"X-Access-Token": "open-sesame"})
    else:
        r = client.get("/v1/time", params={"url": OLD_TWEET, "token": "open-sesame"})
    assert r.status_code == 200


def test_wrong_token(client, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "open-sesame")
    r = client.post("/v1/time", json={"url": OLD_TWEET, "token": "wrong"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"
