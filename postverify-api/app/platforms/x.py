"""X (Twitter) — timestamp offline, images from og:image.

The timestamp requires no fetch at all: the status ID is a snowflake whose
upper 41 bits are a millisecond timestamp.

Images need one plain HTTP call. Note that on a text-only tweet og:image
carries the author's profile picture rather than any post media, so that has to
be filtered out.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult

from .. import fetch
from ..snowflake import InvalidSnowflakeError, x_created_at
from .base import ImageRef, Match, Platform, PlatformError, Timing

_PATH = re.compile(r"^/(?P<user>[A-Za-z0-9_]{1,15})/status(?:es)?/(?P<id>\d{1,25})")
_MEDIA = re.compile(
    r"pbs\.twimg\.com/(media|tweet_video_thumb|ext_tw_video_thumb|amplify_video_thumb)/")
_AVATAR = re.compile(r"/profile_images/|/profile_banners/")


def pick_media(html: str) -> list[ImageRef]:
    out: list[ImageRef] = []
    for url in fetch.og_images(html):
        if _AVATAR.search(url):
            continue
        if _MEDIA.search(url):
            big = re.sub(r"[?&]name=\w+", "", url)
            out.append(ImageRef(big + ("&" if "?" in big else "?") + "name=large",
                                "post", "tweet image"))
        else:
            out.append(ImageRef(url, "page", "found on the page"))
    return out


class X(Platform):
    id = "x"
    label = "X (Twitter)"
    hosts = frozenset({"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com",
                       "vxtwitter.com", "fxtwitter.com", "fixupx.com"})
    sample_url = "https://x.com/NASA/status/1935477485525180417"
    time_method = "id-embedded"
    time_note = "Upper 41 bits of the status ID — offline, no network call"
    image_note = "From og:image, with profile pictures filtered out"
    needs_browser = False

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        m = _PATH.match(parts.path.rstrip("/") or "/")
        if not m:
            return None
        canonical = f"https://x.com/{m['user']}/status/{m['id']}"
        return Match(m["id"], canonical, canonical, {"username": m["user"]})

    async def published_at(self, match: Match, ctx: dict) -> Timing:
        try:
            return Timing(x_created_at(match.post_id), self.time_method, "millisecond")
        except InvalidSnowflakeError as e:
            raise PlatformError(str(e), platform=self.id, reason="invalid_id") from e

    async def load(self, match: Match) -> dict:
        # The timestamp is available offline; the page is only needed for images.
        return {}

    async def images(self, match: Match, ctx: dict) -> list[ImageRef]:
        if "html" not in ctx:
            try:
                ctx["html"] = await fetch.get_html(match.render_url)
            except fetch.FetchError as e:
                raise PlatformError(str(e), platform=self.id,
                                    reason="upstream_error") from e

        found = pick_media(ctx["html"])
        if not any(i.tier == "post" for i in found):
            raise PlatformError(
                "This tweet carries no image; it may be text only",
                platform=self.id, reason="no_media")
        return found
