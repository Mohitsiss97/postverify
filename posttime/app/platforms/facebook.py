"""Facebook service.

Instagram jaisa hi: content server-side aata hi nahi, browser me render karne pe
embedded JSON me creation_time milta hai. Ye URL forms test karke chalte mile —
sab ek hi post pe same timestamp dete hain:

    /<page>/posts/<numeric id>      /reel/<id>
    /<numeric id>                   /watch/?v=<id>

Token (FB_ACCESS_TOKEN) ho to Graph API pehle — turant, aur uska contract stable
hai. Par wo sirf un posts ka time deta hai jinka access token ke paas ho.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult, parse_qs

from ..resolvers import browser
from ..resolvers import facebook_page as page
from ..resolvers import graph
from .base import Match, Platform, ResolutionError, Timing

_ID = r"[0-9]{6,25}"
_TOKEN = r"[A-Za-z0-9]{6,120}"

# (regex, jis group me id hai) — path pe try hote hain
_PATHS = (
    re.compile(rf"^/(?:[^/]+)/posts/(?P<id>{_TOKEN})"),
    re.compile(rf"^/(?:[^/]+)/videos/(?:[^/]+/)?(?P<id>{_ID})"),
    re.compile(rf"^/(?:reel|videos|photos)/(?P<id>{_ID})"),
    re.compile(rf"^/share/[pvr]/(?P<id>{_TOKEN})"),
    re.compile(rf"^/(?P<id>{_ID})$"),
)

# query me id — permalink.php / story.php / photo.php / watch
_QUERY_KEYS = ("story_fbid", "fbid", "v", "id")


class Facebook(Platform):
    id = "facebook"
    label = "Facebook"
    hosts = frozenset({"facebook.com", "m.facebook.com", "fb.com", "fb.watch",
                       "web.facebook.com", "mbasic.facebook.com"})
    sample_url = "https://www.facebook.com/NASA/posts/1615702003258503"
    method = "headless-page"
    precision = "second"
    how = ("Post page ko headless browser me render karke embedded JSON ka creation_time "
           "padha jaata hai — bina login ke. ~8 second lagta hai.")
    setup = "FB_ACCESS_TOKEN"
    setup_optional = True
    setup_note = ("Chalne ke liye machine pe Chrome ya Edge hona chahiye (CHROME_PATH se "
                  "override kar sakte ho). FB_ACCESS_TOKEN optional hai — apne Page ke "
                  "posts uske through turant aayenge.")

    def ready(self) -> bool:
        return browser.available() or self.configured()

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        path = parts.path.rstrip("/") or "/"

        if host == "fb.watch":
            token = path.lstrip("/")
            return Match(token, url, {"render_url": url}) if token else None

        for pattern in _PATHS:
            m = pattern.match(path)
            if m:
                return Match(m["id"], url, {"render_url": url})

        query = parse_qs(parts.query)
        for key in _QUERY_KEYS:
            value = (query.get(key) or [""])[0]
            if re.fullmatch(_ID, value):
                return Match(value, url, {"render_url": url})
        return None

    async def timing(self, post_id: str, extra: dict) -> Timing:
        if self.configured():
            try:
                return Timing(await graph.facebook_created_time(post_id),
                              "api", self.precision)
            except graph.GraphError:
                pass          # token ke dayre se bahar — browser se try karo

        render_url = extra.get("render_url")
        try:
            dt, _field = await page.published_at(render_url, expect_id=post_id)
        except browser.BrowserNotAvailableError as e:
            raise ResolutionError(
                f"{e} Ya phir FB_ACCESS_TOKEN set kijiye (wo sirf apne Page ke posts "
                f"ke liye kaam karega).",
                platform=self.id, reason="not_configured") from e
        except page.NotVisibleError as e:
            raise ResolutionError(str(e), platform=self.id, reason="not_visible") from e
        except page.PageError as e:
            raise ResolutionError(str(e), platform=self.id, reason="upstream_error") from e
        return Timing(dt, "headless-page", self.precision)
