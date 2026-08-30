"""URL detection aur DOM se image nikalna."""
import pytest

from app import media as reg
from app.media import UnsupportedURLError
from app.media.facebook import pick_media as fb_pick
from app.media.instagram import pick_media as ig_pick
from app.media.linkedin import pick_media as li_pick
from app.media.x import pick_media as x_pick


# ---------------- URL detection ----------------

@pytest.mark.parametrize("url,platform,pid", [
    ("https://x.com/NASA/status/1935477485525180417", "x", "1935477485525180417"),
    ("https://twitter.com/a_b/status/123456789012345678?s=20", "x", "123456789012345678"),
    ("https://www.instagram.com/p/DceLPdrCR3L/", "instagram", "DceLPdrCR3L"),
    ("https://www.instagram.com/reel/DcmQE56lLTI/", "instagram", "DcmQE56lLTI"),
    ("https://www.facebook.com/NASA/posts/1615702003258503", "facebook", "1615702003258503"),
    ("https://www.facebook.com/reel/1766270574681470/", "facebook", "1766270574681470"),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "youtube", "jNQXAC9IVRw"),
    ("https://youtu.be/jNQXAC9IVRw?t=9", "youtube", "jNQXAC9IVRw"),
    ("https://www.linkedin.com/feed/update/urn:li:activity:7250000000000000000/",
     "linkedin", "7250000000000000000"),
])
def test_detect(url, platform, pid):
    source, m = reg.detect(url)
    assert (source.id, m.post_id) == (platform, pid)


@pytest.mark.parametrize("url", ["", "https://example.com/x", "https://x.com/NASA", "junk"])
def test_detect_rejects(url):
    with pytest.raises(UnsupportedURLError):
        reg.detect(url)


def test_match_on_is_platform_specific():
    x = reg.get("x")
    assert reg.match_on(x, "https://x.com/a/status/123456789012345678") is not None
    assert reg.match_on(x, "https://www.instagram.com/p/DceLPdrCR3L/") is None


def test_every_sample_matches_its_own_platform(monkeypatch):
    monkeypatch.delenv("PLATFORMS", raising=False)
    for s in reg.catalog():
        detected, _ = reg.detect(s.sample_url)
        assert detected.id == s.id


def test_platforms_env_narrows(monkeypatch):
    monkeypatch.setenv("PLATFORMS", "x,youtube")
    assert [s.id for s in reg.enabled()] == ["x", "youtube"]
    with pytest.raises(KeyError):
        reg.get("instagram")


# ---------------- Instagram DOM ----------------

# Asli DOM se liye gaye prefixes: -15 post ki media, -19 profile pic
IG_DOM = (
    '<meta property="og:image" content="https://scontent.cdninstagram.com/v/t51.82787-15/'
    '786889295_18641156686049152_5453_n.jpg?stp=dst-jpg_e35_p1080x1080">'
    '<img src="https://scontent.cdninstagram.com/v/t51.82787-15/'
    '786889295_18641156686049152_5453_n.jpg?stp=dst-jpg_e35_p640x640">'
    '<img src="https://scontent.cdninstagram.com/v/t51.82787-15/'
    '999000111_18600000000000000_1234_n.jpg?stp=dst-jpg_e35_p1080x1080">'
    '<img src="https://scontent.cdninstagram.com/v/t51.2885-19/'
    '29090066_159271188110124_1152_n.jpg">'
    '<img src="https://scontent.cdninstagram.com/v/t51.82787-19/'
    '11112222_18500000000000000_9999_n.jpg">'
)


def test_instagram_skips_profile_pictures():
    refs = ig_pick(IG_DOM)
    urls = " ".join(r.url for r in refs)
    assert "t51.2885-19" not in urls
    assert "t51.82787-19" not in urls


def test_instagram_marks_og_image_as_post_tier():
    refs = ig_pick(IG_DOM)
    post = [r for r in refs if r.tier == "post"]
    assert len(post) == 1 and "786889295" in post[0].url


def test_instagram_keeps_other_media_as_page_tier():
    """Carousel ki doosri slide ho sakti hai, ya related post — isliye alag tier."""
    refs = ig_pick(IG_DOM)
    page = [r for r in refs if r.tier == "page"]
    assert len(page) == 1 and "999000111" in page[0].url


def test_instagram_picks_biggest_size_of_each_image():
    """Ek hi image 640 aur 1080 me hai — 1080 chunni chahiye."""
    refs = ig_pick(IG_DOM)
    post = next(r for r in refs if r.tier == "post")
    assert "p1080x1080" in post.url


def test_instagram_empty_dom():
    assert ig_pick("<html></html>") == []


# ---------------- X DOM ----------------

def test_x_skips_avatar_and_takes_media():
    dom = ('<meta property="og:image" content="https://pbs.twimg.com/media/Abc123.jpg?name=small">'
           '<meta name="twitter:image" content="https://pbs.twimg.com/profile_images/1/x_400x400.jpg">')
    refs = x_pick(dom)
    assert len(refs) == 1
    assert "media/Abc123" in refs[0].url and refs[0].tier == "post"


def test_x_asks_for_the_large_version():
    dom = '<meta property="og:image" content="https://pbs.twimg.com/media/Abc.jpg?name=small">'
    assert "name=large" in x_pick(dom)[0].url


def test_x_text_only_tweet_has_no_media():
    dom = '<meta property="og:image" content="https://pbs.twimg.com/profile_images/1/a_400x400.jpg">'
    assert [r for r in x_pick(dom) if r.tier == "post"] == []


# ---------------- Facebook / LinkedIn DOM ----------------

def test_facebook_skips_ui_assets():
    dom = ('<meta property="og:image" content="https://scontent.fdel40-1.fna.fbcdn.net/v/t15.5256-10/789.jpg">'
           '<img src="https://scontent.xx.fbcdn.net/hads-ak-prn2/1487645_601.png">'
           '<img src="https://static.xx.fbcdn.net/rsrc.php/v3/y.png">')
    refs = fb_pick(dom)
    assert len(refs) == 1 and refs[0].tier == "post" and "t15.5256-10" in refs[0].url


def test_linkedin_skips_favicon_and_logos():
    dom = ('<meta property="og:image" content="https://static.licdn.com/favicon.ico">'
           '<img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare.jpg">'
           '<img src="https://media.licdn.com/dms/image/company-logo_100_100/x.png">')
    refs = li_pick(dom)
    assert len(refs) == 1 and "feedshare" in refs[0].url


def test_linkedin_short_link_is_explained():
    with pytest.raises(UnsupportedURLError, match="short link"):
        reg.detect("https://lnkd.in/abc123")
