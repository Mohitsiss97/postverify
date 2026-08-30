"""X / Twitter service — 100% offline.

Status ID ek snowflake hai; upper 41 bits me millisecond timestamp baitha hai.
Matlab koi network call nahi, koi key nahi, koi rate limit nahi.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult

from ..resolvers.snowflake import InvalidSnowflakeError, x_created_at
from .base import Match, Platform, ResolutionError, Timing

_PATH = re.compile(r"^/(?P<user>[A-Za-z0-9_]{1,15})/status(?:es)?/(?P<id>\d{1,25})")


class X(Platform):
    id = "x"
    label = "X (Twitter)"
    hosts = frozenset({
        "x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com",
        "vxtwitter.com", "fxtwitter.com", "fixupx.com",
    })
    sample_url = "https://x.com/elonmusk/status/1026872652290379776"
    method = "id-embedded"
    precision = "millisecond"
    how = "Status ID khud ek snowflake hai — uske upper 41 bits hi timestamp hain. Offline."

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        m = _PATH.match(parts.path.rstrip("/") or "/")
        if not m:
            return None
        return Match(m["id"], f"https://x.com/{m['user']}/status/{m['id']}",
                     {"username": m["user"]})

    async def timing(self, post_id: str, extra: dict) -> Timing:
        try:
            return Timing(x_created_at(post_id), self.method, self.precision)
        except InvalidSnowflakeError as e:
            raise ResolutionError(str(e), platform=self.id, reason="invalid_id") from e
