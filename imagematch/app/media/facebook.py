"""Facebook — images rendered DOM se.

Instagram jaisa hi: HTML server se khali aata hai. og:image post ki media (ya
video ka cover frame) deti hai. Baaki fbcdn images page ki ho sakti hain, isliye
wo "page" tier me jaati hain.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult, parse_qs

from .. import browser, fetch
from .base import ExtractionError, ImageRef, Match, Source

_ID = r"[0-9]{6,25}"
_TOKEN = r"[A-Za-z0-9]{6,120}"
_PATHS = (
    re.compile(rf"^/(?:[^/]+)/posts/(?P<id>{_TOKEN})"),
    re.compile(rf"^/(?:[^/]+)/videos/(?:[^/]+/)?(?P<id>{_ID})"),
    re.compile(rf"^/(?:reel|videos|photos)/(?P<id>{_ID})"),
    re.compile(rf"^/share/[pvr]/(?P<id>{_TOKEN})"),
    re.compile(rf"^/(?P<id>{_ID})$"),
)
_QUERY_KEYS = ("story_fbid", "fbid", "v", "id")

_IMG = re.compile(r'<img[^>]+src="(https://[^"]+)"')
_CDN = re.compile(r"fbcdn\.net|cdninstagram\.com")
# hads-ak / static assets FB ke apne UI ke hain, post ke nahi
_JUNK = re.compile(r"/hads-ak|/rsrc\.php|static\.xx\.fbcdn\.net")


def pick_media(html: str) -> list[ImageRef]:
    out: list[ImageRef] = []
    seen: set[str] = set()
    for url in fetch.og_images(html):
        key = url.split("?")[0]
        if key not in seen:
            seen.add(key)
            out.append(ImageRef(url, "post", "post ki main image"))
    for url in _IMG.findall(html):
        key = url.split("?")[0]
        if key in seen or not _CDN.search(url) or _JUNK.search(url):
            continue
        seen.add(key)
        out.append(ImageRef(url, "page", "post page pe mili"))
    return out


class Facebook(Source):
    id = "facebook"
    label = "Facebook"
    hosts = frozenset({"facebook.com", "m.facebook.com", "fb.com", "fb.watch",
                       "web.facebook.com", "mbasic.facebook.com"})
    sample_url = "https://www.facebook.com/NASA/posts/1615702003258503"
    how = ("Post page browser me render karke uski CDN images nikaali jaati hain. "
           "Video post ho to uska cover frame. ~8 second lagta hai.")
    needs_browser = True

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        path = parts.path.rstrip("/") or "/"
        if host == "fb.watch":
            token = path.lstrip("/")
            return Match(token, url, url) if token else None
        for pattern in _PATHS:
            m = pattern.match(path)
            if m:
                return Match(m["id"], url, url)
        query = parse_qs(parts.query)
        for key in _QUERY_KEYS:
            value = (query.get(key) or [""])[0]
            if re.fullmatch(_ID, value):
                return Match(value, url, url)
        return None

    async def images(self, match: Match) -> list[ImageRef]:
        try:
            dom = await browser.render(match.render_url)
        except browser.BrowserNotAvailableError as e:
            raise ExtractionError(str(e), platform=self.id, reason="not_configured") from e
        except browser.BrowserError as e:
            raise ExtractionError(str(e), platform=self.id, reason="upstream_error") from e

        found = pick_media(dom)
        if not found:
            if re.search(r"login_form|You must log in", dom, re.I):
                raise ExtractionError(
                    "Facebook ne login maanga — ye post public nahi hai",
                    platform=self.id, reason="not_visible")
            raise ExtractionError(
                "Is post pe koi image nahi mili", platform=self.id, reason="no_media")
        return found
