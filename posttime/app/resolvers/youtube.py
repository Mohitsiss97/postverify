"""YouTube resolver — do raaste, dono bina user login ke.

1. published_at()           Data API v3. Key chahiye. Stable contract, quota-backed.
2. published_at_from_page()  Public watch page ka uploadDate. Kuch nahi chahiye.

Watch page pe upload time <meta itemprop="uploadDate"> me poora baitha hota hai —
seconds aur timezone offset ke saath. Wahi cheez jo aap browser me bina login ke
dekh sakte ho. Isliye key optional hai, zaroori nahi.

Trade-off: page ka markup YouTube kabhi bhi badal sakta hai. API ka contract nahi
badalta. Isliye key ho to API pehle, aur page fallback rehta hai.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import httpx

_API = "https://www.googleapis.com/youtube/v3/videos"
_WATCH = "https://www.youtube.com/watch"
_TIMEOUT = 15.0

# Real browser jaisa UA — warna YouTube consent/bot page bhej deta hai.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Ek hi timestamp page pe kai jagah aata hai; jo pehle mile wahi le lo.
_PAGE_PATTERNS = (
    re.compile(r'itemprop="uploadDate"[^>]*content="([^"]+)"'),
    re.compile(r'"uploadDate"\s*:\s*"([^"]+)"'),
    re.compile(r'"publishDate"\s*:\s*"([^"]+)"'),
)


class YouTubeError(RuntimeError):
    pass


class NotConfiguredError(YouTubeError):
    pass


class NotFoundError(YouTubeError):
    """Video hai hi nahi, ya private/deleted hai."""


def _parse(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


async def published_at(video_id: str, *, client: httpx.AsyncClient | None = None) -> datetime:
    """Data API v3 — key se."""
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        raise NotConfiguredError("YOUTUBE_API_KEY set nahi hai")

    owns = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        r = await client.get(_API, params={"part": "snippet", "id": video_id, "key": key})
        if r.status_code == 403:
            raise YouTubeError("YouTube API quota khatam ya key restricted hai")
        r.raise_for_status()
        items = r.json().get("items") or []
        if not items:
            raise NotFoundError("Video mila nahi — private, deleted, ya galat ID")
        return _parse(items[0]["snippet"]["publishedAt"])
    except httpx.HTTPError as e:
        raise YouTubeError(f"YouTube API reachable nahi: {e}") from e
    finally:
        if owns:
            await client.aclose()


async def published_at_from_page(video_id: str, *,
                                 client: httpx.AsyncClient | None = None) -> datetime:
    """Public watch page ka uploadDate — koi key nahi, koi login nahi."""
    owns = client is None
    client = client or httpx.AsyncClient(
        timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA})
    try:
        r = await client.get(_WATCH, params={"v": video_id})
        if r.status_code == 404:
            raise NotFoundError("Video mila nahi")
        r.raise_for_status()
        html = r.text
        for pattern in _PAGE_PATTERNS:
            m = pattern.search(html)
            if m:
                return _parse(m.group(1))
        if "Video unavailable" in html or "isPlayable\":false" in html:
            raise NotFoundError("Video unavailable — private, deleted, ya region-blocked")
        raise YouTubeError(
            "Watch page pe uploadDate nahi mila. YouTube ne markup badal diya ho sakta "
            "hai — YOUTUBE_API_KEY set karke API waala raasta use kijiye.")
    except httpx.HTTPError as e:
        raise YouTubeError(f"YouTube page reachable nahi: {e}") from e
    finally:
        if owns:
            await client.aclose()
