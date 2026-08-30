"""YouTube — time public watch page se, image thumbnail.

Watch page pe upload time poora baitha hota hai, wahi jo bina login ke dikhta hai:

    <meta itemprop="uploadDate" content="2009-10-24T23:57:33-07:00">

Video ki andar ki frames nahi dekhi jaatin — sirf thumbnail. Frames ke liye poora
video download karna padta, jo bahut mehenga hai.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from urllib.parse import ParseResult, parse_qs

from .. import fetch
from .base import ImageRef, Match, Platform, PlatformError, Timing

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH = re.compile(r"^/(?:shorts|live|embed|v)/(?P<id>[A-Za-z0-9_-]{11})")
_QUALITIES = ("maxresdefault", "sddefault", "hqdefault", "mqdefault")

_DATE_PATTERNS = (
    re.compile(r'itemprop="uploadDate"[^>]*content="([^"]+)"'),
    re.compile(r'"uploadDate"\s*:\s*"([^"]+)"'),
    re.compile(r'"publishDate"\s*:\s*"([^"]+)"'),
)
_API = "https://www.googleapis.com/youtube/v3/videos"


class YouTube(Platform):
    id = "youtube"
    label = "YouTube"
    hosts = frozenset({"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"})
    sample_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    time_method = "public-page"
    time_note = "Watch page ka uploadDate — koi key nahi chahiye"
    image_note = "Video ka thumbnail (andar ki frames nahi)"
    needs_browser = False
    optional_env = "YOUTUBE_API_KEY"

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
        watch = f"https://www.youtube.com/watch?v={vid}"
        return Match(vid, watch, watch)

    async def load(self, match: Match) -> dict:
        return {}

    async def published_at(self, match: Match, ctx: dict) -> Timing:
        # Key ho to API pehle — uska contract stable hai. Warna public page.
        if self.configured():
            dt = await self._from_api(match.post_id)
            if dt is not None:
                return Timing(dt, "api", "second")

        if "html" not in ctx:
            try:
                ctx["html"] = await fetch.get_html(match.render_url)
            except fetch.FetchError as e:
                raise PlatformError(str(e), platform=self.id, reason="upstream_error") from e

        for pattern in _DATE_PATTERNS:
            m = pattern.search(ctx["html"])
            if m:
                dt = datetime.fromisoformat(
                    m.group(1).replace("Z", "+00:00")).astimezone(timezone.utc)
                return Timing(dt, self.time_method, "second")

        if "Video unavailable" in ctx["html"]:
            raise PlatformError("Video unavailable — private, deleted, ya region-blocked",
                                platform=self.id, reason="not_visible")
        raise PlatformError(
            "Watch page pe uploadDate nahi mila — YouTube ne markup badal diya ho sakta hai",
            platform=self.id, reason="upstream_error")

    async def _from_api(self, video_id: str) -> datetime | None:
        """API fail ho jaye to None — page fallback chalta rahega."""
        import httpx
        key = os.getenv("YOUTUBE_API_KEY")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(_API, params={"part": "snippet", "id": video_id,
                                                   "key": key})
                r.raise_for_status()
                items = r.json().get("items") or []
                if not items:
                    return None
                raw = items[0]["snippet"]["publishedAt"]
                return datetime.fromisoformat(
                    raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None


    async def images(self, match: Match, ctx: dict) -> list[ImageRef]:
        # Ek hi image, alag resolutions me — same group, taaki pehli jo download
        # ho jaye wahi use ho aur chaaron compare na hon.
        return [ImageRef(f"https://i.ytimg.com/vi/{match.post_id}/{q}.jpg",
                         "post", "video ka thumbnail", group="thumbnail")
                for q in _QUALITIES]
