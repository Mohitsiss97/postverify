"""YouTube — video ka thumbnail.

Video ki har frame se compare karna bahut mehenga hai (video download + frame
sampling). Isliye ye service video ka **thumbnail** dekhti hai — cover image.
Thumbnail URLs video ID se seedha ban jaate hain, koi page fetch nahi chahiye.

maxres har video pe nahi hota, isliye bade se chhote ki taraf try karte hain.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult, parse_qs

from .base import ImageRef, Match, Source

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH = re.compile(r"^/(?:shorts|live|embed|v)/(?P<id>[A-Za-z0-9_-]{11})")

_QUALITIES = ("maxresdefault", "sddefault", "hqdefault", "mqdefault")


class YouTube(Source):
    id = "youtube"
    label = "YouTube"
    hosts = frozenset({"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"})
    sample_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    how = ("Video ka thumbnail (cover image) compare hota hai — video ki andar ki "
           "frames nahi. Browser ki zaroorat nahi, ~1 second.")
    needs_browser = False

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        path = parts.path.rstrip("/") or "/"

        if host == "youtu.be":
            vid = path.lstrip("/")[:11]
            return self._make(vid) if _ID.match(vid) else None

        m = _PATH.match(path)
        if m:
            return self._make(m["id"])

        vid = (parse_qs(parts.query).get("v") or [""])[0]
        return self._make(vid) if _ID.match(vid) else None

    @staticmethod
    def _make(vid: str) -> Match:
        return Match(vid, f"https://www.youtube.com/watch?v={vid}",
                     f"https://www.youtube.com/watch?v={vid}")

    async def images(self, match: Match) -> list[ImageRef]:
        # Ek hi image, alag resolutions me. Same group me daal dete hain taaki
        # service pehli jo download ho jaye wahi le, chaaron compare na kare.
        return [ImageRef(f"https://i.ytimg.com/vi/{match.post_id}/{q}.jpg",
                         "post", "video ka thumbnail", group="thumbnail")
                for q in _QUALITIES]
