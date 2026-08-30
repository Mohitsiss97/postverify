"""YouTube — key ke bina public page se, key ho to API se."""
from datetime import datetime, timezone

import httpx
import pytest

from app import platforms as reg
from app.platforms import ResolutionError
from app.resolvers import youtube as yt
from app.service import resolve_with

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Asli watch page me time isi shape me aata hai (probe karke confirm kiya).
PAGE_META = '<meta itemprop="uploadDate" content="2009-10-24T23:57:33-07:00">'
PAGE_JSON = '{"microformat":{"playerMicroformatRenderer":{"uploadDate":"2020-01-02T03:04:05-00:00"}}}'

EXPECTED_META = datetime(2009, 10, 25, 6, 57, 33, tzinfo=timezone.utc)   # -07:00 -> UTC


def _client(body: str, status: int = 200) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, text=body)))


# ---------------- page resolver ----------------

async def test_page_meta_tag():
    dt = await yt.published_at_from_page("dQw4w9WgXcQ", client=_client(PAGE_META))
    assert dt == EXPECTED_META


async def test_page_json_fallback():
    """Meta tag na ho to embedded JSON se bhi nikal aata hai."""
    dt = await yt.published_at_from_page("x", client=_client(PAGE_JSON))
    assert dt.year == 2020 and dt.month == 1


async def test_page_timezone_offset_converted_to_utc():
    dt = await yt.published_at_from_page("x", client=_client(PAGE_META))
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 6              # 23:57 -07:00 agle din 06:57 UTC


async def test_page_unavailable_video():
    with pytest.raises(yt.NotFoundError):
        await yt.published_at_from_page("x", client=_client("<html>Video unavailable</html>"))


async def test_page_markup_change_says_so():
    """Markup badal jaye to chup na rahe — key waala raasta suggest kare."""
    with pytest.raises(yt.YouTubeError, match="YOUTUBE_API_KEY"):
        await yt.published_at_from_page("x", client=_client("<html>kuch aur</html>"))


async def test_page_404():
    with pytest.raises(yt.NotFoundError):
        await yt.published_at_from_page("x", client=_client("", status=404))


# ---------------- platform wiring ----------------

@pytest.fixture()
def no_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("PLATFORMS", raising=False)


async def test_without_key_uses_public_page(no_key, monkeypatch):
    async def fake_page(video_id, *, client=None):
        assert video_id == "dQw4w9WgXcQ"
        return EXPECTED_META

    async def boom(*a, **k):
        raise AssertionError("key nahi hai, API call nahi honi chahiye")

    monkeypatch.setattr(yt, "published_at_from_page", fake_page)
    monkeypatch.setattr(yt, "published_at", boom)

    r = await resolve_with(reg.get("youtube"), VIDEO, tz="Asia/Kolkata")
    assert r.method == "public-page"
    assert r.published_at == EXPECTED_META
    assert r.published_at_local.startswith("2009-10-25T12:27:33")   # IST


async def test_with_key_prefers_api(no_key, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    async def fake_api(video_id, *, client=None):
        return EXPECTED_META

    async def boom(*a, **k):
        raise AssertionError("key hai, page scrape nahi hona chahiye")

    monkeypatch.setattr(yt, "published_at", fake_api)
    monkeypatch.setattr(yt, "published_at_from_page", boom)

    r = await resolve_with(reg.get("youtube"), VIDEO)
    assert r.method == "api"


async def test_api_quota_falls_back_to_page(no_key, monkeypatch):
    """Quota khatam ho jaye to service band nahi hoti — page se chalti rehti hai."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    async def quota_dead(*a, **k):
        raise yt.YouTubeError("YouTube API quota khatam ya key restricted hai")

    async def fake_page(video_id, *, client=None):
        return EXPECTED_META

    monkeypatch.setattr(yt, "published_at", quota_dead)
    monkeypatch.setattr(yt, "published_at_from_page", fake_page)

    r = await resolve_with(reg.get("youtube"), VIDEO)
    assert r.method == "public-page"


async def test_deleted_video_is_404_not_500(no_key, monkeypatch):
    async def gone(video_id, *, client=None):
        raise yt.NotFoundError("Video mila nahi")

    monkeypatch.setattr(yt, "published_at_from_page", gone)
    with pytest.raises(ResolutionError) as e:
        await resolve_with(reg.get("youtube"), VIDEO)
    assert e.value.reason == "not_visible"
