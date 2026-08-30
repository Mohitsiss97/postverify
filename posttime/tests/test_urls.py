"""URL parsing aur platform detection."""
import pytest

from app import platforms as reg
from app.platforms import UnsupportedURLError


@pytest.mark.parametrize("url,platform,pid", [
    ("https://x.com/elonmusk/status/1026872652290379776", "x", "1026872652290379776"),
    ("https://twitter.com/jack/status/20", "x", "20"),
    ("https://mobile.twitter.com/a_b/status/123456789012345678?s=20", "x", "123456789012345678"),
    ("x.com/user/status/999888777666555444", "x", "999888777666555444"),
    ("https://www.linkedin.com/posts/mohit_hello-world-activity-7100000000000000000-AbCd",
     "linkedin", "7100000000000000000"),
    ("https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000000/",
     "linkedin", "7100000000000000000"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=42", "youtube", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "youtube", "dQw4w9WgXcQ"),
    ("https://www.instagram.com/p/CxYzAbC1234/", "instagram", "CxYzAbC1234"),
    ("https://www.instagram.com/reel/CxYzAbC1234/", "instagram", "CxYzAbC1234"),
    ("https://www.instagram.com/someuser/p/CxYzAbC1234/", "instagram", "CxYzAbC1234"),
    ("https://www.facebook.com/NASA/posts/1615702003258503", "facebook", "1615702003258503"),
    ("https://www.facebook.com/reel/1766270574681470/", "facebook", "1766270574681470"),
    ("https://www.facebook.com/watch/?v=1766270574681470", "facebook", "1766270574681470"),
    ("https://www.facebook.com/permalink.php?story_fbid=1615702003258503&id=99",
     "facebook", "1615702003258503"),
    ("https://fb.watch/aB3dEf9x/", "facebook", "aB3dEf9x"),
])
def test_detect(url, platform, pid):
    p, m = reg.detect(url)
    assert (p.id, m.post_id) == (platform, pid)


@pytest.mark.parametrize("url", [
    "", "   ", "https://example.com/post/1", "https://x.com/elonmusk",
    "not a url", "https://www.youtube.com/", "https://www.instagram.com/someuser/",
])
def test_detect_rejects(url):
    with pytest.raises(UnsupportedURLError):
        reg.detect(url)


def test_lnkdin_short_link_explained():
    with pytest.raises(UnsupportedURLError, match="short link"):
        reg.detect("https://lnkd.in/abc123")


def test_match_on_is_platform_specific():
    """X ki service ko Instagram ka link nahi lena chahiye."""
    x = reg.get("x")
    assert reg.match_on(x, "https://x.com/a/status/1026872652290379776") is not None
    assert reg.match_on(x, "https://www.instagram.com/p/CxYzAbC1234/") is None


def test_canonical_url_normalised():
    _, m = reg.detect("https://mobile.twitter.com/jack/status/1026872652290379776?s=20")
    assert m.canonical_url == "https://x.com/jack/status/1026872652290379776"


# ---------------- registry ----------------

def test_catalog_has_every_platform():
    assert {p.id for p in reg.catalog()} == {"x", "linkedin", "youtube", "instagram", "facebook"}


def test_default_deployment_enables_all(monkeypatch):
    monkeypatch.delenv("PLATFORMS", raising=False)
    assert len(reg.enabled()) == len(reg.catalog())


def test_platforms_env_narrows_deployment(monkeypatch):
    monkeypatch.setenv("PLATFORMS", "x, linkedin")
    assert [p.id for p in reg.enabled()] == ["x", "linkedin"]
    with pytest.raises(KeyError):
        reg.get("youtube")


def test_platforms_env_rejects_typos(monkeypatch):
    monkeypatch.setenv("PLATFORMS", "twitter")
    with pytest.raises(RuntimeError, match="unknown platform"):
        reg.enabled()


def test_every_platform_declares_its_own_sample(monkeypatch):
    """Sample URL apne hi platform pe match hona chahiye — picker isi pe bharosa karta hai."""
    monkeypatch.delenv("PLATFORMS", raising=False)
    for p in reg.catalog():
        detected, _ = reg.detect(p.sample_url)
        assert detected.id == p.id, f"{p.id} ka sample {detected.id} pe match hua"
