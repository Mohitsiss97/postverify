"""Instagram service.

Do raaste, dono me user se kuch nahi maanga jaata:

  headless-page  Public post page ko browser me render karke uska <time> padho.
                 Kisi bhi public post pe chalta hai. ~6 second lagta hai.
  api            Graph API. Token chahiye, aur wo sirf apne hi account ke posts
                 dikhata hai — par turant aur stable hai.

Token ho to Graph pehle; jo post uske dayre se bahar ho uske liye browser.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult

from ..resolvers import browser, graph
from ..resolvers import instagram_page as page
from .base import Match, Platform, ResolutionError, Timing

_PATH = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/(?P<code>[A-Za-z0-9_-]+)")


class Instagram(Platform):
    id = "instagram"
    label = "Instagram"
    hosts = frozenset({"instagram.com", "instagr.am", "ddinstagram.com"})
    sample_url = "https://www.instagram.com/p/DceLPdrCR3L/"
    method = "headless-page"
    precision = "second"
    how = ("Post page ko headless browser me render karke uska <time> padha jaata hai — "
           "bina login ke. Instagram ka HTML server se khali aata hai, isliye browser "
           "zaroori hai. ~6 second lagta hai.")
    setup = "IG_ACCESS_TOKEN"
    setup_optional = True
    setup_note = ("Chalne ke liye machine pe Chrome ya Edge hona chahiye (CHROME_PATH se "
                  "override kar sakte ho). IG_ACCESS_TOKEN optional hai — wo set karoge to "
                  "apne account ke posts Graph API se turant aayenge, browser sirf baaki "
                  "posts ke liye chalega.")

    def ready(self) -> bool:
        return browser.available() or self.configured()

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        m = _PATH.match(parts.path.rstrip("/") or "/")
        if not m:
            return None
        return Match(m["code"], f"https://www.instagram.com/p/{m['code']}/")

    async def timing(self, post_id: str, extra: dict) -> Timing:
        # Token hai to pehle Graph — turant aur stable. Jo post uske dayre se
        # bahar hai uske liye browser fallback.
        if self.configured():
            try:
                dt = await graph.instagram_timestamp(post_id)
                return Timing(dt, "api", self.precision)
            except graph.NotVisibleError:
                pass
            except graph.GraphError:
                pass

        try:
            dt = await page.published_at(post_id)
        except browser.BrowserNotAvailableError as e:
            raise ResolutionError(
                f"{e} Ya phir IG_ACCESS_TOKEN set kijiye (wo sirf apne account ke "
                f"posts ke liye kaam karega).",
                platform=self.id, reason="not_configured") from e
        except page.NotVisibleError as e:
            raise ResolutionError(str(e), platform=self.id, reason="not_visible") from e
        except page.PageError as e:
            raise ResolutionError(str(e), platform=self.id, reason="upstream_error") from e
        return Timing(dt, "headless-page", self.precision)
