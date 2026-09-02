"""HTTP contract — prepare, media, verify, aur cleanup."""
import cv2
import pytest
from fastapi.testclient import TestClient

from app import fetch
from app import platforms as reg
from app.main import app
from app.platforms import ImageRef, PlatformError
from app.store import store
from tests.test_compare import jpg, make_image

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
    """Post pe ek image — na browser chale, na asli download."""
    async def images(self, match, ctx):
        return [ImageRef("https://cdn.example/post.jpg", "post", "post ki main image")]

    async def get_image(url, client=None):
        return POST_IMAGE

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)


def prepare(client, url, tz="Asia/Kolkata"):
    return client.post("/prepare", data={"url": url, "tz": tz})


def verify(client, image=None, *, url=None, session=None, tz="Asia/Kolkata"):
    data = {"tz": tz}
    if url:
        data["url"] = url
    if session:
        data["session"] = session
    files = {"image": ("a.jpg", image, "image/jpeg")} if image is not None else None
    return client.post("/verify", data=data, files=files)


# ---------------- meta ----------------

def test_health_reports_the_store(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and "x" in body["platforms"]
    assert body["store"]["sessions"] >= 0 and body["store"]["ttl_seconds"] > 0


def test_platform_list_includes_hosts(client):
    body = client.get("/platforms").json()
    assert [p["id"] for p in body["platforms"]] == \
        ["x", "instagram", "facebook", "linkedin", "youtube"]
    x = next(p for p in body["platforms"] if p["id"] == "x")
    assert "x.com" in x["hosts"] and x["needs_browser"] is False


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "PostVerify" in r.text


# ---------------- step 1: prepare ----------------

def test_prepare_returns_time_and_local_image_urls(client, stub_images):
    d = prepare(client, TWEET).json()
    assert d["platform"] == "x"
    assert d["session"]
    assert d["time"]["published_at"]
    assert len(d["images"]) == 1
    img = d["images"][0]
    assert img["url"] == f"/media/{d['session']}/{img['name']}"
    assert img["tier"] == "post"
    assert img["bytes"] == len(POST_IMAGE)


def test_prepared_image_is_served_from_our_own_origin(client, stub_images):
    """Yahi poora point hai — CDN block ho to bhi image dikhni chahiye."""
    d = prepare(client, TWEET).json()
    r = client.get(d["images"][0]["url"])
    assert r.status_code == 200
    assert r.content == POST_IMAGE
    assert r.headers["cache-control"] == "no-store"


def test_media_rejects_path_traversal(client, stub_images):
    d = prepare(client, TWEET).json()
    for bad in ("../../secret.txt", "..%2Fsecret", "0.exe", "x.jpg"):
        assert client.get(f"/media/{d['session']}/{bad}").status_code == 404


def test_media_of_unknown_session_is_404(client):
    assert client.get("/media/nahi-hai/0.jpg").status_code == 404


def test_prepare_on_text_only_post_still_gives_time(client, monkeypatch):
    async def no_images(self, match, ctx):
        raise PlatformError("Is tweet me koi image nahi hai",
                            platform="x", reason="no_media")

    monkeypatch.setattr(reg.get("x").__class__, "images", no_images)
    d = prepare(client, TWEET).json()
    assert d["time"] is not None
    assert d["images"] == [] and d["image_error"]


# ---------------- step 2: verify ----------------

def test_session_verify_finds_the_image(client, stub_images):
    p = prepare(client, TWEET).json()
    d = verify(client, POST_IMAGE, session=p["session"]).json()
    assert d["present"] is True
    assert d["verdict"] == "identical" and d["score"] == 100
    assert d["time"] is not None, "session me rakha hua time bhi wapas aana chahiye"


def test_session_verify_needs_no_network(client, stub_images, monkeypatch):
    """Images pehle se downloaded hain — check me ek bhi call nahi jaani chahiye."""
    p = prepare(client, TWEET).json()

    async def boom(*a, **k):
        raise AssertionError("check me network call nahi honi chahiye")

    monkeypatch.setattr(fetch, "get_image", boom)
    monkeypatch.setattr(fetch, "get_html", boom)

    d = verify(client, POST_IMAGE, session=p["session"]).json()
    assert d["present"] is True


def test_resized_image_still_found(client, stub_images):
    p = prepare(client, TWEET).json()
    small = cv2.resize(make_image(21), (200, 200))
    d = verify(client, jpg(small), session=p["session"]).json()
    assert d["present"] is True and d["score"] >= 70


def test_different_image_not_found(client, stub_images):
    p = prepare(client, TWEET).json()
    d = verify(client, jpg(make_image(99)), session=p["session"]).json()
    assert d["present"] is False and d["matched"] is None
    assert d["score"] < 40 and "nahi mili" in d["summary"]


def test_best_of_many_images_wins(client, monkeypatch):
    slides = {f"https://cdn.example/{i}.jpg": jpg(make_image(30 + i)) for i in range(1, 4)}

    async def images(self, match, ctx):
        return [ImageRef(u, "post" if i == 0 else "page", f"slide {i + 1}")
                for i, u in enumerate(slides)]

    async def get_image(url, client=None):
        return slides[url]

    monkeypatch.setattr(reg.get("x").__class__, "images", images)
    monkeypatch.setattr(fetch, "get_image", get_image)

    p = prepare(client, TWEET).json()
    assert len(p["images"]) == 3
    d = verify(client, jpg(make_image(33)), session=p["session"]).json()
    assert d["present"] is True and d["images_checked"] == 3


# ---------------- cleanup ----------------

def test_check_deletes_everything(client, stub_images):
    p = prepare(client, TWEET).json()
    session = store.get(p["session"])
    directory = session.directory
    assert directory.exists() and any(directory.iterdir())

    d = verify(client, POST_IMAGE, session=p["session"]).json()
    assert d["cleaned_up"] is True
    assert not directory.exists(), "session folder rehna nahi chahiye"
    assert store.get(p["session"]) is None
    assert client.get(p["images"][0]["url"]).status_code == 404


def test_expired_session_says_so(client, stub_images):
    p = prepare(client, TWEET).json()
    store.drop(p["session"])
    r = verify(client, POST_IMAGE, session=p["session"])
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "expired"


def test_session_can_be_dropped_by_hand(client, stub_images):
    p = prepare(client, TWEET).json()
    assert client.delete(f"/session/{p['session']}").json()["deleted"] is True
    assert client.delete(f"/session/{p['session']}").json()["deleted"] is False
    assert store.get(p["session"]) is None


def test_ttl_sweeps_abandoned_sessions(client, stub_images, monkeypatch):
    """User URL daal ke chala gaya — data hamesha ke liye nahi pada rehna chahiye."""
    monkeypatch.setenv("PREVIEW_TTL_SEC", "30")
    p = prepare(client, TWEET).json()
    session = store.get(p["session"])
    directory = session.directory
    session.created -= 100          # ghadi aage badha do

    assert store.get(p["session"]) is None
    assert not directory.exists()


def test_url_verify_cleans_up_immediately(client, stub_images):
    before = store.stats()["sessions"]
    d = verify(client, POST_IMAGE, url=TWEET).json()
    assert d["present"] is True and d["cleaned_up"] is True
    assert store.stats()["sessions"] == before, "koi session peeche nahi chhootna chahiye"


def test_url_only_leaves_nothing_behind(client, stub_images):
    before = store.stats()["sessions"]
    d = verify(client, url=TWEET).json()
    assert d["time"] is not None and d["image_checked"] is False
    assert d["cleaned_up"] is True
    assert store.stats()["sessions"] == before


def test_failed_prepare_leaves_nothing_behind(client, monkeypatch):
    async def broken(self, match, ctx):
        raise PlatformError("kuch nahi", platform="x", reason="upstream_error")

    monkeypatch.setattr(reg.get("x").__class__, "published_at", broken)
    monkeypatch.setattr(reg.get("x").__class__, "images", broken)

    before = store.stats()["sessions"]
    assert prepare(client, TWEET).status_code == 502
    assert store.stats()["sessions"] == before


def test_uploaded_image_never_touches_disk(client, stub_images):
    """User ki image sirf memory me — store me sirf post ki images honi chahiye."""
    p = prepare(client, TWEET).json()
    session = store.get(p["session"])
    files_before = sorted(f.name for f in session.directory.iterdir())

    verify(client, jpg(make_image(55)), session=p["session"])
    # session ab delete ho chuka; uske andar bhi sirf post ki image thi
    assert files_before == ["0.jpg"]


# ---------------- errors ----------------

def test_unsupported_url(client):
    r = verify(client, url="https://example.com/nope")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsupported_url"


def test_junk_upload_rejected(client, stub_images):
    p = prepare(client, TWEET).json()
    r = verify(client, b"ye image nahi hai", session=p["session"])
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_image"


def test_verify_needs_url_or_session(client):
    r = client.post("/verify", data={"tz": "UTC"})
    assert r.status_code == 400


def test_session_without_image_is_refused(client, stub_images):
    p = prepare(client, TWEET).json()
    r = verify(client, session=p["session"])
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_image"


def test_pre_snowflake_tweet(client):
    r = verify(client, url="https://twitter.com/jack/status/20")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "invalid_id"


def test_no_browser_is_503(client, monkeypatch):
    from app import browser
    monkeypatch.setattr(browser, "available", lambda: False)
    monkeypatch.setattr(browser, "chrome_path", lambda: None)
    r = prepare(client, "https://www.instagram.com/p/DceLPdrCR3L/")
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "not_configured"


# ---------------- access token (public deployment) ----------------

def test_open_by_default(client, stub_images):
    """Local use pe koi token nahi chahiye."""
    assert client.get("/health").json()["locked"] is False
    assert prepare(client, TWEET).status_code == 200


def test_token_locks_the_expensive_endpoints(client, monkeypatch, stub_images):
    monkeypatch.setenv("ACCESS_TOKEN", "khulja-sim-sim")
    assert client.get("/health").json()["locked"] is True

    assert prepare(client, TWEET).status_code == 401
    assert verify(client, POST_IMAGE, url=TWEET).status_code == 401


def test_right_token_gets_through(client, monkeypatch, stub_images):
    monkeypatch.setenv("ACCESS_TOKEN", "khulja-sim-sim")
    r = client.post("/prepare", data={"url": TWEET, "token": "khulja-sim-sim"})
    assert r.status_code == 200


def test_token_also_works_as_a_header(client, monkeypatch, stub_images):
    monkeypatch.setenv("ACCESS_TOKEN", "khulja-sim-sim")
    r = client.post("/prepare", data={"url": TWEET},
                    headers={"X-Access-Token": "khulja-sim-sim"})
    assert r.status_code == 200


def test_wrong_token_refused(client, monkeypatch, stub_images):
    monkeypatch.setenv("ACCESS_TOKEN", "khulja-sim-sim")
    r = client.post("/prepare", data={"url": TWEET, "token": "galat"})
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "unauthorized"
