"""LinkedIn — time offline, images render se.

Activity URN bhi snowflake-shaped hai, bas epoch plain Unix hai. Matlab time ke
liye koi network call nahi.

Images ke liye browser chahiye: plain HTTP pe LinkedIn og:image me apna favicon
bhej deta hai (test karke dekha), asli media licdn CDN pe hoti hai.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult

from .. import browser, fetch
from ..snowflake import InvalidSnowflakeError, linkedin_created_at
from .base import ImageRef, Match, Platform, PlatformError, Timing, UnsupportedURLError

_URN = re.compile(r"(?:activity[:-]|ugcPost[:-]|share[:-])(?P<id>\d{15,25})")
_IMG = re.compile(r'<img[^>]+src="(https://[^"]+)"')
_MEDIA = re.compile(r"media\.licdn\.com/dms/image/")
_JUNK = re.compile(r"static\.licdn\.com|/favicon|ghost|profile-displayphoto|company-logo")


def pick_media(html: str) -> list[ImageRef]:
    out: list[ImageRef] = []
    seen: set[str] = set()
    for url in fetch.og_images(html):
        if _JUNK.search(url) or not _MEDIA.search(url):
            continue
        seen.add(url.split("?")[0])
        out.append(ImageRef(url, "post", "post ki main image"))
    for url in _IMG.findall(html):
        key = url.split("?")[0]
        if key in seen or not _MEDIA.search(url) or _JUNK.search(url):
            continue
        seen.add(key)
        out.append(ImageRef(url, "page", "post page pe mili"))
    return out


class LinkedIn(Platform):
    id = "linkedin"
    label = "LinkedIn"
    hosts = frozenset({"linkedin.com", "in.linkedin.com", "lnkd.in"})
    sample_url = ("https://www.linkedin.com/feed/update/"
                  "urn:li:activity:7250000000000000000/")
    time_method = "id-embedded"
    time_note = "Activity URN ke upper 41 bits — offline, koi network call nahi"
    image_note = "Page render karke licdn CDN ki images"
    needs_browser = True

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        m = _URN.search(parts.path) or _URN.search(parts.query)
        if m:
            canonical = (f"https://www.linkedin.com/feed/update/"
                         f"urn:li:activity:{m['id']}/")
            return Match(m["id"], canonical, canonical)
        if host == "lnkd.in":
            raise UnsupportedURLError(
                "lnkd.in short link hai — browser me kholkar full /posts/ ya "
                "/feed/update/ URL dijiye")
        return None

    async def published_at(self, match: Match, ctx: dict) -> Timing:
        try:
            return Timing(linkedin_created_at(match.post_id), self.time_method,
                          "millisecond")
        except InvalidSnowflakeError as e:
            raise PlatformError(str(e), platform=self.id, reason="invalid_id") from e

    async def load(self, match: Match) -> dict:
        # Time offline mil jaata hai; render sirf images ke liye chahiye.
        return {}


    async def images(self, match: Match, ctx: dict) -> list[ImageRef]:
        if "dom" not in ctx:
            try:
                ctx["dom"] = await browser.render(match.render_url)
            except browser.BrowserNotAvailableError as e:
                raise PlatformError(str(e), platform=self.id, reason="not_configured") from e
            except browser.BrowserError as e:
                raise PlatformError(str(e), platform=self.id, reason="upstream_error") from e

        found = pick_media(ctx["dom"])
        if not found:
            if re.search(r"authwall|sign in to see|/login", ctx["dom"], re.I):
                raise PlatformError("LinkedIn ne authwall dikhaya — post public nahi hai",
                                    platform=self.id, reason="not_visible")
            raise PlatformError("Is post pe koi image nahi mili",
                                platform=self.id, reason="no_media")
        return found
