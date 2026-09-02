"""Instagram — headless page se, aur token ho to Graph API se."""
from datetime import datetime, timedelta, timezone

import pytest

from app import platforms as reg
from app.platforms import ResolutionError
from app.resolvers import browser, graph
from app.resolvers import instagram_page as page
from app.service import resolve_with

POST = "https://www.instagram.com/p/DceLPdrCR3L/"
KNOWN = datetime(2026, 8, 25, 17, 29, 13, tzinfo=timezone.utc)

# Asli rendered DOM ka shape: pehla <time> post ka, baaki comments ke.
DOM = (
    '<article><time class="x1" datetime="2026-08-25T17:29:13.000Z" title="Aug 25, 2026">'
    '</time></article>'
    '<ul><time datetime="2026-08-28T15:40:24.000Z"></time>'
    '<time datetime="2026-08-29T01:15:58.000Z"></time></ul>'
)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for var in ("IG_ACCESS_TOKEN", "PLATFORMS", "CHROME_PATH"):
        monkeypatch.delenv(var, raising=False)
    _clear_cache()
    yield
    _clear_cache()


def _clear_cache():
    # monkeypatch chrome_path ko plain function se badal deta hai — tab cache_clear nahi hota
    clear = getattr(browser.chrome_path, "cache_clear", None)
    if clear:
        clear()


# ---------------- DOM extraction ----------------

def test_extract_takes_the_post_time_not_a_comment():
    assert page.extract(DOM) == KNOWN


def test_extract_converts_to_utc():
    dt = page.extract('<time datetime="2026-08-25T17:29:13.000+05:30"></time>')
    assert dt.tzinfo == timezone.utc and dt.hour == 11


def test_extract_login_wall():
    with pytest.raises(page.NotVisibleError, match="login"):
        page.extract('<div id="login_form">Log in</div>')


def test_extract_deleted_post():
    with pytest.raises(page.NotVisibleError):
        page.extract("<div>Sorry, this page isn't available.</div>")


def test_extract_markup_change_points_at_the_token_route():
    with pytest.raises(page.PageError, match="IG_ACCESS_TOKEN"):
        page.extract("<div>kuch aur hi markup</div>")


def test_extract_rejects_impossible_dates():
    """Instagram Oct 2010 me bana — usse pehle ka time matlab galat element pakda."""
    with pytest.raises(page.PageError, match="bharosemand"):
        page.extract('<time datetime="2004-01-01T00:00:00.000Z"></time>')

    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    with pytest.raises(page.PageError, match="bharosemand"):
        page.extract(f'<time datetime="{future}"></time>')


# ---------------- platform wiring ----------------

async def test_uses_browser_when_no_token(monkeypatch):
    async def fake_render(url):
        assert url == POST
        return DOM

    monkeypatch.setattr(browser, "render", fake_render)
    monkeypatch.setattr(browser, "available", lambda: True)

    r = await resolve_with(reg.get("instagram"), POST, tz="Asia/Kolkata")
    assert r.method == "headless-page"
    assert r.published_at == KNOWN
    assert r.precision == "second"


async def test_no_browser_and_no_token_says_so(monkeypatch):
    monkeypatch.setattr(browser, "available", lambda: False)
    monkeypatch.setattr(browser, "chrome_path", lambda: None)

    with pytest.raises(ResolutionError) as e:
        await resolve_with(reg.get("instagram"), POST)
    assert e.value.reason == "not_configured"
    assert "CHROME_PATH" in str(e.value) or "Chrome" in str(e.value)


async def test_token_route_preferred(monkeypatch):
    monkeypatch.setenv("IG_ACCESS_TOKEN", "fake")

    async def fake_graph(shortcode, client=None):
        return KNOWN

    async def boom(url):
        raise AssertionError("Graph chal gaya, browser nahi chalna chahiye")

    monkeypatch.setattr(graph, "instagram_timestamp", fake_graph)
    monkeypatch.setattr(browser, "render", boom)

    r = await resolve_with(reg.get("instagram"), POST)
    assert r.method == "api"


async def test_falls_back_to_browser_when_post_outside_token_scope(monkeypatch):
    """Graph sirf apne account ke posts deta hai — baaki ke liye browser."""
    monkeypatch.setenv("IG_ACCESS_TOKEN", "fake")

    async def not_mine(shortcode, client=None):
        raise graph.NotVisibleError("Graph API ne mana kiya")

    async def fake_render(url):
        return DOM

    monkeypatch.setattr(graph, "instagram_timestamp", not_mine)
    monkeypatch.setattr(browser, "render", fake_render)
    monkeypatch.setattr(browser, "available", lambda: True)

    r = await resolve_with(reg.get("instagram"), POST)
    assert r.method == "headless-page" and r.published_at == KNOWN


async def test_render_timeout_is_upstream_error(monkeypatch):
    async def slow(url):
        raise browser.RenderTimeoutError("Page 30s me render nahi hua")

    monkeypatch.setattr(browser, "render", slow)
    monkeypatch.setattr(browser, "available", lambda: True)

    with pytest.raises(ResolutionError) as e:
        await resolve_with(reg.get("instagram"), POST)
    assert e.value.reason == "upstream_error"


# ---------------- browser discovery ----------------

def test_chrome_path_honours_env(monkeypatch, tmp_path):
    fake = tmp_path / "chrome.exe"
    fake.write_text("")
    monkeypatch.setenv("CHROME_PATH", str(fake))
    _clear_cache()
    assert browser.chrome_path() == str(fake)


def test_chrome_path_env_pointing_nowhere(monkeypatch):
    monkeypatch.setenv("CHROME_PATH", "/nahi/hai/chrome")
    _clear_cache()
    assert browser.chrome_path() is None
    assert browser.available() is False
