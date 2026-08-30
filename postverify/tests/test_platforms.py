"""URL auto-detection, time extraction, aur media picking."""
from datetime import datetime, timezone

import pytest

from app import platforms as reg
from app.platforms import PlatformError, UnsupportedURLError
from app.platforms.facebook import extract_time as fb_time
from app.platforms.facebook import pick_media as fb_media
from app.platforms.instagram import extract_time as ig_time
from app.platforms.instagram import pick_media as ig_media
from app.platforms.linkedin import pick_media as li_media
from app.platforms.x import pick_media as x_media
from app.snowflake import x_created_at


# ---------------- auto-detection ----------------

@pytest.mark.parametrize("url,platform,pid", [
    ("https://x.com/NASA/status/1935477485525180417", "x", "1935477485525180417"),
    ("https://twitter.com/jack/status/20", "x", "20"),
    ("x.com/a_b/status/123456789012345678", "x", "123456789012345678"),
    ("https://www.instagram.com/p/DceLPdrCR3L/", "instagram", "DceLPdrCR3L"),
    ("https://www.instagram.com/reel/DcmQE56lLTI/", "instagram", "DcmQE56lLTI"),
    ("https://www.facebook.com/NASA/posts/1615702003258503", "facebook", "1615702003258503"),
    ("https://www.facebook.com/reel/1766270574681470/", "facebook", "1766270574681470"),
    ("https://www.facebook.com/watch/?v=1766270574681470", "facebook", "1766270574681470"),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "youtube", "jNQXAC9IVRw"),
    ("https://youtu.be/jNQXAC9IVRw?t=9", "youtube", "jNQXAC9IVRw"),
    ("https://www.youtube.com/shorts/xvFZjo5PgG0", "youtube", "xvFZjo5PgG0"),
    ("https://www.linkedin.com/feed/update/urn:li:activity:7250000000000000000/",
     "linkedin", "7250000000000000000"),
    ("https://www.linkedin.com/posts/mohit_hello-activity-7100000000000000000-AbCd",
     "linkedin", "7100000000000000000"),
])
def test_url_alone_picks_the_platform(url, platform, pid):
    """User ko kuch chunna nahi padta — URL hi kaafi hai."""
    p, m = reg.detect(url)
    assert (p.id, m.post_id) == (platform, pid)


@pytest.mark.parametrize("url", ["", "   ", "https://example.com/x", "https://x.com/NASA", "junk"])
def test_unknown_urls_rejected(url):
    with pytest.raises(UnsupportedURLError):
        reg.detect(url)


def test_unsupported_url_lists_what_works():
    with pytest.raises(UnsupportedURLError, match="Instagram"):
        reg.detect("https://example.com/post/1")


def test_every_sample_detects_itself(monkeypatch):
    monkeypatch.delenv("PLATFORMS", raising=False)
    for p in reg.catalog():
        detected, _ = reg.detect(p.sample_url)
        assert detected.id == p.id


def test_lnkdin_short_link_explained():
    with pytest.raises(UnsupportedURLError, match="short link"):
        reg.detect("https://lnkd.in/abc123")


def test_platforms_env_narrows(monkeypatch):
    monkeypatch.setenv("PLATFORMS", "x,youtube")
    assert [p.id for p in reg.enabled()] == ["x", "youtube"]


# ---------------- offline time ----------------

async def test_x_time_is_offline():
    p, m = reg.detect("https://x.com/elonmusk/status/1026872652290379776")
    t = await p.published_at(m, {})
    assert t.published_at == datetime(2018, 8, 7, 16, 48, 13, 334000, tzinfo=timezone.utc)
    assert t.method == "id-embedded"


async def test_linkedin_time_is_offline():
    p, m = reg.detect("https://www.linkedin.com/feed/update/urn:li:activity:7250000000000000000/")
    assert (await p.published_at(m, {})).published_at.year == 2024


async def test_pre_snowflake_tweet_refused():
    p, m = reg.detect("https://twitter.com/jack/status/20")
    with pytest.raises(PlatformError) as e:
        await p.published_at(m, {})
    assert e.value.reason == "invalid_id"


# ---------------- Instagram DOM ----------------

IG_DOM = (
    '<article><time datetime="2026-08-25T17:29:13.000Z"></time></article>'
    '<time datetime="2026-08-28T15:40:24.000Z"></time>'
    '<meta property="og:image" content="https://scontent.cdninstagram.com/v/t51.82787-15/'
    '786889295_18641156686049152_5453_n.jpg?stp=dst-jpg_e35_p1080x1080">'
    '<img src="https://scontent.cdninstagram.com/v/t51.82787-15/'
    '786889295_18641156686049152_5453_n.jpg?stp=dst-jpg_e35_p640x640">'
    '<img src="https://scontent.cdninstagram.com/v/t51.82787-15/'
    '999000111_18600000000000000_1234_n.jpg?stp=dst-jpg_e35_p1080x1080">'
    '<img src="https://scontent.cdninstagram.com/v/t51.2885-19/29090066_159_1152_n.jpg">'
)


def test_instagram_time_is_the_first_one():
    """Pehla <time> post ka, baaki comments ke."""
    assert ig_time(IG_DOM) == datetime(2026, 8, 25, 17, 29, 13, tzinfo=timezone.utc)


def test_instagram_time_rejects_pre_2010():
    with pytest.raises(PlatformError):
        ig_time('<time datetime="2004-01-01T00:00:00.000Z"></time>')


def test_instagram_skips_profile_pictures():
    assert "t51.2885-19" not in " ".join(r.url for r in ig_media(IG_DOM))


def test_instagram_prefers_the_biggest_size():
    post = next(r for r in ig_media(IG_DOM) if r.tier == "post")
    assert "p1080x1080" in post.url


def test_instagram_other_media_is_page_tier():
    page = [r for r in ig_media(IG_DOM) if r.tier == "page"]
    assert len(page) == 1 and "999000111" in page[0].url


# ---------------- Facebook DOM ----------------

FB_DOM = ('{"post_id":"1615702003258503","creation_time":1788012882,'
          '"publish_time":1788012882}'
          '<meta property="og:image" content="https://scontent.fdel40-1.fna.fbcdn.net/v/t15/1.jpg">'
          '<img src="https://static.xx.fbcdn.net/rsrc.php/v3/y.png">')


def test_facebook_time_from_embedded_json():
    dt, field = fb_time(FB_DOM, "1615702003258503")
    assert dt == datetime(2026, 8, 29, 14, 14, 42, tzinfo=timezone.utc)
    assert field == "creation_time"


def test_facebook_refuses_when_post_id_missing():
    with pytest.raises(PlatformError, match="mila hi nahi"):
        fb_time('{"post_id":"999","creation_time":1788012882}', "1615702003258503")


def test_facebook_refuses_ambiguous_timestamps():
    dom = '"1615702003258503" "creation_time":1788012882 "creation_time":1788019272'
    with pytest.raises(PlatformError):
        fb_time(dom, "1615702003258503")


def test_facebook_skips_ui_assets():
    refs = fb_media(FB_DOM)
    assert len(refs) == 1 and refs[0].tier == "post"


# ---------------- X / LinkedIn media ----------------

def test_x_skips_avatar():
    dom = ('<meta property="og:image" content="https://pbs.twimg.com/media/Abc.jpg?name=small">'
           '<meta name="twitter:image" content="https://pbs.twimg.com/profile_images/1/a.jpg">')
    refs = x_media(dom)
    assert len(refs) == 1 and "name=large" in refs[0].url


def test_x_text_only_tweet_has_no_post_image():
    dom = '<meta property="og:image" content="https://pbs.twimg.com/profile_images/1/a.jpg">'
    assert [r for r in x_media(dom) if r.tier == "post"] == []


def test_linkedin_skips_favicon():
    dom = ('<meta property="og:image" content="https://static.licdn.com/favicon.ico">'
           '<img src="https://media.licdn.com/dms/image/v2/D4D/feedshare.jpg">')
    refs = li_media(dom)
    assert len(refs) == 1 and "feedshare" in refs[0].url


def test_snowflake_anchor_still_holds():
    """Real anchor — agar ye toota to maths galat hai."""
    assert x_created_at(1026872652290379776).year == 2018
