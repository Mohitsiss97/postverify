"""Instagram — ek hi render se time aur images dono.

Instagram ka HTML server se khali aata hai, isliye browser chahiye. Us ek render
me dono cheezein hain:

    time    <time datetime="2026-08-25T17:29:13.000Z">  (DOM ka pehla <time>)
    images  scontent CDN URLs, prefix t51.*-15

Prefix se pata chalta hai image ka type: -15 post ki media, -19 aur t51.2885-19
profile pictures.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import ParseResult

from .. import browser, fetch
from .base import ImageRef, Match, Platform, PlatformError, Timing

_PATH = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/(?P<code>[A-Za-z0-9_-]+)")
_TIME = re.compile(r'<time[^>]*\sdatetime="([^"]+)"')
_IMG = re.compile(r'<img[^>]+src="(https://[^"]+)"')
_MEDIA_PREFIX = re.compile(r"/v/t\d+\.\d+-15/")
_FILE_ID = re.compile(r"/(\d+)_(\d+)_(\d+)_n\.")
_WIDTH = re.compile(r"[?&]stp=[^&]*?[pe](\d{3,4})x\d{3,4}")
_LOGIN = re.compile(r"login_form|Log in to see", re.I)
_GONE = re.compile(r"Sorry, this page isn.t available|Post not available", re.I)

# Instagram October 2010 me launch hua — usse pehle ka post ho hi nahi sakta.
_FLOOR = datetime(2010, 10, 1, tzinfo=timezone.utc)


def _file_key(url: str) -> str:
    m = _FILE_ID.search(url)
    return m.group(1) if m else url.split("?")[0]


def _width(url: str) -> int:
    m = _WIDTH.search(url)
    return int(m.group(1)) if m else 0


def extract_time(dom: str) -> datetime:
    """DOM ka pehla <time> post ka apna hai, baaki comments aur related posts ke."""
    m = _TIME.search(dom)
    if not m:
        raise PlatformError(
            "Page pe koi <time> nahi mila — Instagram ne DOM badal diya ho sakta hai",
            platform="instagram", reason="upstream_error")
    dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).astimezone(timezone.utc)
    if not _FLOOR <= dt <= datetime.now(timezone.utc) + timedelta(days=1):
        raise PlatformError(
            f"Nikla hua time bharosemand nahi lagta ({dt.isoformat()})",
            platform="instagram", reason="upstream_error")
    return dt


def pick_media(html: str) -> list[ImageRef]:
    og = fetch.og_images(html)
    in_page = [u for u in _IMG.findall(html) if _MEDIA_PREFIX.search(u)]

    # Ek hi image kai sizes me aati hai — file id pe group karke sabse bada
    # version rakhte hain, chhoti copy se compare karna kamzor hota hai.
    best: dict[str, str] = {}
    for url in og + in_page:
        key = _file_key(url)
        if key not in best or _width(url) > _width(best[key]):
            best[key] = url

    og_keys: list[str] = []
    for url in og:
        key = _file_key(url)
        if key not in og_keys:
            og_keys.append(key)

    out = [ImageRef(best[key], "post", "post ki main image") for key in og_keys]
    out += [ImageRef(url, "page", "post page pe mili")
            for key, url in best.items() if key not in og_keys]
    return out


class Instagram(Platform):
    id = "instagram"
    label = "Instagram"
    hosts = frozenset({"instagram.com", "instagr.am", "ddinstagram.com"})
    sample_url = "https://www.instagram.com/p/DceLPdrCR3L/"
    time_method = "headless-page"
    time_note = "Page browser me render karke uska <time> element"
    image_note = "Usi render se CDN images (profile pics filter karke)"
    needs_browser = True

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        m = _PATH.match(parts.path.rstrip("/") or "/")
        if not m:
            return None
        canonical = f"https://www.instagram.com/p/{m['code']}/"
        return Match(m["code"], canonical, canonical)

    async def load(self, match: Match) -> dict:
        try:
            dom = await browser.render(match.render_url)
        except browser.BrowserNotAvailableError as e:
            raise PlatformError(str(e), platform=self.id, reason="not_configured") from e
        except browser.BrowserError as e:
            raise PlatformError(str(e), platform=self.id, reason="upstream_error") from e

        if not _TIME.search(dom) and not _MEDIA_PREFIX.search(dom):
            if _GONE.search(dom):
                raise PlatformError("Post available nahi hai — delete ya private ho sakta hai",
                                    platform=self.id, reason="not_visible")
            if _LOGIN.search(dom):
                raise PlatformError(
                    "Instagram ne login maanga — ye post public nahi hai",
                    platform=self.id, reason="not_visible")
        return {"dom": dom}

    async def published_at(self, match: Match, ctx: dict) -> Timing:
        return Timing(extract_time(ctx["dom"]), self.time_method, "second")


    async def images(self, match: Match, ctx: dict) -> list[ImageRef]:
        found = pick_media(ctx["dom"])
        if not found:
            raise PlatformError("Is post pe koi image nahi mili",
                                platform=self.id, reason="no_media")
        return found
