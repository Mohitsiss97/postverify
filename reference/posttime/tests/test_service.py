"""Service layer — platform chuna hua ho ya khud detect karna ho."""
import pytest

from app import platforms as reg
from app.platforms import ResolutionError, UnsupportedURLError, WrongPlatformError
from app.service import resolve, resolve_with

TWEET = "https://x.com/elonmusk/status/1026872652290379776"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("PLATFORMS", "YOUTUBE_API_KEY", "IG_ACCESS_TOKEN", "FB_ACCESS_TOKEN"):
        monkeypatch.delenv(var, raising=False)


# ---------------- chosen platform ----------------

async def test_x_service_resolves():
    r = await resolve_with(reg.get("x"), TWEET, tz="Asia/Kolkata")
    assert r.platform == "x"
    assert r.method == "id-embedded"
    assert r.published_at.year == 2018
    assert r.published_at_local.startswith("2018-08-07T22:18")     # IST = UTC+5:30
    assert r.timezone == "Asia/Kolkata"
    assert r.age_seconds > 0
    assert "purana" in r.age_human


async def test_linkedin_service_resolves():
    r = await resolve_with(
        reg.get("linkedin"),
        "https://www.linkedin.com/feed/update/urn:li:activity:7250000000000000000/")
    assert r.platform == "linkedin" and r.published_at.year == 2024


async def test_bad_timezone_ignored_not_fatal():
    r = await resolve_with(reg.get("x"), TWEET, tz="Mars/Olympus")
    assert r.published_at_local is None and r.timezone is None


# ---------------- galat platform ----------------

async def test_wrong_platform_is_refused():
    """Instagram ki service ko X ka link diya — chup-chaap resolve nahi hona chahiye."""
    with pytest.raises(WrongPlatformError) as e:
        await resolve_with(reg.get("instagram"), TWEET)
    assert (e.value.expected, e.value.actual) == ("instagram", "x")


async def test_junk_url_gets_platform_specific_hint():
    with pytest.raises(UnsupportedURLError) as e:
        await resolve_with(reg.get("youtube"), "https://example.com/hello")
    assert reg.get("youtube").sample_url in str(e.value)


# ---------------- setup missing ----------------

async def test_no_browser_no_token_reports_not_configured(monkeypatch):
    """Chrome bhi nahi, token bhi nahi — to saaf bataye ki kya chahiye."""
    from app.resolvers import browser
    monkeypatch.setattr(browser, "available", lambda: False)
    monkeypatch.setattr(browser, "chrome_path", lambda: None)

    with pytest.raises(ResolutionError) as e:
        await resolve_with(reg.get("facebook"),
                           "https://www.facebook.com/NASA/posts/1615702003258503")
    assert e.value.reason == "not_configured"
    assert e.value.platform == "facebook"


# ---------------- auto detect ----------------

async def test_auto_detect_resolves():
    r = await resolve(TWEET)
    assert r.platform == "x"


async def test_auto_detect_respects_deployment(monkeypatch):
    monkeypatch.setenv("PLATFORMS", "youtube")
    with pytest.raises(ResolutionError) as e:
        await resolve(TWEET)
    assert e.value.reason == "disabled"
