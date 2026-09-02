"""Facebook — headless page se creation_time, token ho to Graph API."""
from datetime import datetime, timezone

import pytest

from app import platforms as reg
from app.platforms import ResolutionError
from app.resolvers import browser, graph
from app.resolvers import facebook_page as page
from app.service import resolve_with

PID = "1615702003258503"
POST = f"https://www.facebook.com/NASA/posts/{PID}"
KNOWN = datetime(2026, 8, 29, 14, 14, 42, tzinfo=timezone.utc)   # unix 1788012882

DOM = (f'<div data-id="{PID}"></div>'
       f'{{"post_id":"{PID}","creation_time":1788012882,"publish_time":1788012882}}')


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for var in ("FB_ACCESS_TOKEN", "PLATFORMS", "CHROME_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(browser, "available", lambda: True)


# ---------------- DOM extraction ----------------

def test_extract_creation_time():
    dt, field = page.extract(DOM, expect_id=PID)
    assert dt == KNOWN and field == "creation_time"


def test_extract_falls_back_to_publish_time():
    dom = f'"{PID}" "publish_time":1788012882'
    dt, field = page.extract(dom, expect_id=PID)
    assert dt == KNOWN and field == "publish_time"


def test_extract_data_utime_old_layout():
    dom = f'"{PID}" <abbr data-utime="1788012882"></abbr>'
    dt, field = page.extract(dom, expect_id=PID)
    assert dt == KNOWN and field == "data-utime"


def test_wrong_post_rendered():
    """Requested id DOM me nahi mila — matlab hum galat page pe hain."""
    with pytest.raises(page.NotVisibleError, match="mila hi nahi"):
        page.extract('{"post_id":"999","creation_time":1788012882}', expect_id=PID)


def test_two_timestamps_is_refused_not_guessed():
    """Page listing pe kai posts hote hain — wahan guess karna galat jawab dega."""
    dom = f'"{PID}" "creation_time":1788012882 "creation_time":1788019272'
    with pytest.raises(page.AmbiguousError):
        page.extract(dom, expect_id=PID)


def test_login_wall():
    with pytest.raises(page.NotVisibleError, match="login"):
        page.extract('<form id="login_form"></form>')


def test_no_timestamp_points_at_token_route():
    with pytest.raises(page.PageError, match="FB_ACCESS_TOKEN"):
        page.extract("<div>kuch aur markup</div>")


def test_impossible_timestamps_ignored():
    """2004 se pehle ka ya future ka time Facebook ka ho hi nahi sakta."""
    with pytest.raises(page.PageError):
        page.extract(f'"{PID}" "creation_time":0999999999', expect_id=PID)


# ---------------- URL forms ----------------

@pytest.mark.parametrize("url,pid", [
    (POST, PID),
    ("https://www.facebook.com/reel/1766270574681470/", "1766270574681470"),
    ("https://www.facebook.com/watch/?v=1766270574681470", "1766270574681470"),
    ("https://www.facebook.com/1615702003258503", PID),
    ("https://m.facebook.com/NASA/videos/1766270574681470/", "1766270574681470"),
    ("https://www.facebook.com/story.php?story_fbid=1615702003258503&id=7",
     PID),
])
def test_url_forms(url, pid):
    p, m = reg.detect(url)
    assert p.id == "facebook" and m.post_id == pid
    assert m.extra["render_url"].startswith("http")


# ---------------- platform wiring ----------------

async def test_uses_browser_when_no_token(monkeypatch):
    async def fake_render(url):
        assert url == POST          # permalink hi render ho, page listing nahi
        return DOM

    monkeypatch.setattr(browser, "render", fake_render)

    r = await resolve_with(reg.get("facebook"), POST, tz="Asia/Kolkata")
    assert r.method == "headless-page"
    assert r.published_at == KNOWN
    assert r.published_at_local.startswith("2026-08-29T19:44:42")   # IST


async def test_token_route_preferred(monkeypatch):
    monkeypatch.setenv("FB_ACCESS_TOKEN", "fake")

    async def fake_graph(post_id, client=None):
        return KNOWN

    async def boom(url):
        raise AssertionError("Graph chal gaya, browser nahi chalna chahiye")

    monkeypatch.setattr(graph, "facebook_created_time", fake_graph)
    monkeypatch.setattr(browser, "render", boom)

    r = await resolve_with(reg.get("facebook"), POST)
    assert r.method == "api"


async def test_falls_back_to_browser_outside_token_scope(monkeypatch):
    monkeypatch.setenv("FB_ACCESS_TOKEN", "fake")

    async def not_mine(post_id, client=None):
        raise graph.NotVisibleError("token ke paas access nahi")

    async def fake_render(url):
        return DOM

    monkeypatch.setattr(graph, "facebook_created_time", not_mine)
    monkeypatch.setattr(browser, "render", fake_render)

    r = await resolve_with(reg.get("facebook"), POST)
    assert r.method == "headless-page" and r.published_at == KNOWN


async def test_ambiguous_dom_is_an_error_not_a_wrong_answer(monkeypatch):
    async def two_posts(url):
        return f'"{PID}" "creation_time":1788012882 "creation_time":1788019272'

    monkeypatch.setattr(browser, "render", two_posts)
    with pytest.raises(ResolutionError) as e:
        await resolve_with(reg.get("facebook"), POST)
    assert e.value.reason == "upstream_error"
