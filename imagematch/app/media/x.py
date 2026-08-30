"""X (Twitter) — og:image se, browser ki zaroorat nahi.

X apne og tags plain HTTP pe hi de deta hai. Ek dhyan ki baat: text-only tweet pe
og:image me author ki profile picture aati hai, post ki media nahi. Usse filter
karna zaroori hai — warna har text tweet pe hum profile pic compare karte rahenge.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult

from .. import fetch
from .base import ExtractionError, ImageRef, Match, Source

_PATH = re.compile(r"^/(?P<user>[A-Za-z0-9_]{1,15})/status(?:es)?/(?P<id>\d{1,25})")
# pbs.twimg.com/media/... = tweet ki image; /profile_images/ = avatar
_MEDIA = re.compile(r"pbs\.twimg\.com/(media|tweet_video_thumb|ext_tw_video_thumb|amplify_video_thumb)/")
_AVATAR = re.compile(r"/profile_images/|/profile_banners/")


def pick_media(html: str) -> list[ImageRef]:
    out: list[ImageRef] = []
    for url in fetch.og_images(html):
        if _AVATAR.search(url):
            continue
        if _MEDIA.search(url):
            # ?name=small ko bade version se badal do
            big = re.sub(r"[?&]name=\w+", "", url)
            out.append(ImageRef(big + ("&" if "?" in big else "?") + "name=large",
                                "post", "tweet ki image"))
        else:
            out.append(ImageRef(url, "page", "page pe mili"))
    return out


class X(Source):
    id = "x"
    label = "X (Twitter)"
    hosts = frozenset({"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com",
                       "vxtwitter.com", "fxtwitter.com", "fixupx.com"})
    sample_url = "https://x.com/NASA/status/1935477485525180417"
    how = "Tweet ke og:image tags se — browser ki zaroorat nahi, ~1 second."
    needs_browser = False

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        m = _PATH.match(parts.path.rstrip("/") or "/")
        if not m:
            return None
        canonical = f"https://x.com/{m['user']}/status/{m['id']}"
        return Match(m["id"], canonical, canonical)

    async def images(self, match: Match) -> list[ImageRef]:
        try:
            html = await fetch.get_html(match.render_url)
        except fetch.FetchError as e:
            raise ExtractionError(str(e), platform=self.id, reason="upstream_error") from e

        found = pick_media(html)
        if not any(i.tier == "post" for i in found):
            raise ExtractionError(
                "Is tweet me koi image nahi hai — sirf text ho sakta hai, ya X ne "
                "media chhupa di hai",
                platform=self.id, reason="no_media")
        return found
