"""LinkedIn service — 100% offline.

Activity URN bhi snowflake-shaped hai, bas epoch plain Unix hai (X jaisa custom nahi).
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult

from ..resolvers.snowflake import InvalidSnowflakeError, linkedin_created_at
from .base import Match, Platform, ResolutionError, Timing, UnsupportedURLError

# /posts/<slug>-activity-<id>-<hash>  |  /feed/update/urn:li:activity:<id>
_URN = re.compile(r"(?:activity[:-]|ugcPost[:-]|share[:-])(?P<id>\d{15,25})")


class LinkedIn(Platform):
    id = "linkedin"
    label = "LinkedIn"
    hosts = frozenset({"linkedin.com", "in.linkedin.com", "lnkd.in"})
    sample_url = ("https://www.linkedin.com/feed/update/"
                  "urn:li:activity:7250000000000000000/")
    method = "id-embedded"
    precision = "millisecond"
    how = "Activity URN ke upper 41 bits timestamp hain (Unix epoch). Offline."

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        m = _URN.search(parts.path) or _URN.search(parts.query)
        if m:
            return Match(
                m["id"],
                f"https://www.linkedin.com/feed/update/urn:li:activity:{m['id']}/",
            )
        if host == "lnkd.in":
            raise UnsupportedURLError(
                "lnkd.in short link hai — browser me kholkar full /posts/ ya "
                "/feed/update/ URL copy karke dijiye")
        return None

    async def timing(self, post_id: str, extra: dict) -> Timing:
        try:
            return Timing(linkedin_created_at(post_id), self.method, self.precision)
        except InvalidSnowflakeError as e:
            raise ResolutionError(str(e), platform=self.id, reason="invalid_id") from e
