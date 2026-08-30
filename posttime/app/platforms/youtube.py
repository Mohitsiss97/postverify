"""YouTube service — Data API v3.

Video ID me timestamp nahi hota, isliye ek network call lagti hai.
Key SERVICE ki hai (env var) — user se kuch nahi maanga jaata.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult, parse_qs

from ..resolvers import youtube as api
from .base import Match, Platform, ResolutionError, Timing

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH = re.compile(r"^/(?:shorts|live|embed|v)/(?P<id>[A-Za-z0-9_-]{11})")


class YouTube(Platform):
    id = "youtube"
    label = "YouTube"
    hosts = frozenset({"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"})
    sample_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    method = "public-page"
    precision = "second"
    how = ("Public watch page ke uploadDate se — wahi time jo aap browser me bina login "
           "ke dekhte ho. Key ho to Data API v3 use hoti hai (zyada stable).")
    setup = "YOUTUBE_API_KEY"
    setup_optional = True
    setup_note = ("Key ke bina bhi chalta hai. Key set karoge (YOUTUBE_API_KEY) to "
                  "Data API v3 pehle try hogi — uska contract nahi badalta, jabki page "
                  "ka markup YouTube kabhi bhi badal sakta hai.")

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        path = parts.path.rstrip("/") or "/"

        if host == "youtu.be":
            vid = path.lstrip("/")[:11]
            if _ID.match(vid):
                return Match(vid, f"https://www.youtube.com/watch?v={vid}")
            return None

        m = _PATH.match(path)
        if m:
            return Match(m["id"], f"https://www.youtube.com/watch?v={m['id']}")

        vid = (parse_qs(parts.query).get("v") or [""])[0]
        if _ID.match(vid):
            return Match(vid, f"https://www.youtube.com/watch?v={vid}")
        return None

    async def timing(self, post_id: str, extra: dict) -> Timing:
        """Key ho to API, warna public page. API fail ho jaye to bhi page fallback."""
        if self.configured():
            try:
                return Timing(await api.published_at(post_id), "api", self.precision)
            except api.NotFoundError as e:
                raise ResolutionError(str(e), platform=self.id, reason="not_visible") from e
            except api.YouTubeError:
                pass          # quota khatam / key restricted — page se try karo

        try:
            dt = await api.published_at_from_page(post_id)
        except api.NotFoundError as e:
            raise ResolutionError(str(e), platform=self.id, reason="not_visible") from e
        except api.YouTubeError as e:
            raise ResolutionError(str(e), platform=self.id, reason="upstream_error") from e
        return Timing(dt, "public-page", self.precision)
