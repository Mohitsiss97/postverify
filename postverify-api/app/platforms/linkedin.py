"""LinkedIn — timestamp offline, images from a rendered page.

The activity URN is snowflake-shaped as well, with a plain Unix epoch, so the
timestamp costs no network call.

Images do require a browser: over plain HTTP LinkedIn returns its own favicon
as og:image (confirmed by testing); the real media sits on the licdn CDN and
only appears once the page has rendered.
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
        out.append(ImageRef(url, "post", "main post image"))
    for url in _IMG.findall(html):
        key = url.split("?")[0]
        if key in seen or not _MEDIA.search(url) or _JUNK.search(url):
            continue
        seen.add(key)
        out.append(ImageRef(url, "page", "found on the post page"))
    return out


class LinkedIn(Platform):
    id = "linkedin"
    label = "LinkedIn"
    hosts = frozenset({"linkedin.com", "in.linkedin.com", "lnkd.in"})
    sample_url = ("https://www.linkedin.com/feed/update/"
                  "urn:li:activity:7250000000000000000/")
    time_method = "id-embedded"
    time_note = "Upper 41 bits of the activity URN — offline, no network call"
    image_note = "licdn CDN images, read from the rendered page"
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
                "lnkd.in is a short link. Open it in a browser and supply the "
                "full /posts/ or /feed/update/ URL instead.")
        return None

    async def published_at(self, match: Match, ctx: dict) -> Timing:
        try:
            return Timing(linkedin_created_at(match.post_id), self.time_method,
                          "millisecond")
        except InvalidSnowflakeError as e:
            raise PlatformError(str(e), platform=self.id, reason="invalid_id") from e

    async def load(self, match: Match) -> dict:
        # The timestamp is available offline; rendering is only needed for images.
        return {}

    async def images(self, match: Match, ctx: dict) -> list[ImageRef]:
        if "dom" not in ctx:
            try:
                ctx["dom"] = await browser.render(match.render_url)
            except browser.BrowserNotAvailableError as e:
                raise PlatformError(str(e), platform=self.id,
                                    reason="not_configured") from e
            except browser.BrowserError as e:
                raise PlatformError(str(e), platform=self.id,
                                    reason="upstream_error") from e

        found = pick_media(ctx["dom"])
        if not found:
            if re.search(r"authwall|sign in to see|/login", ctx["dom"], re.I):
                raise PlatformError(
                    "LinkedIn returned an authwall — the post is not public",
                    platform=self.id, reason="not_visible")
            raise PlatformError("No image was found on this post",
                                platform=self.id, reason="no_media")
        return found
