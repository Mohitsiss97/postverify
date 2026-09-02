"""Facebook — both the timestamp and the images come from a single render.

The timestamp lives in embedded JSON; there is no <time> element in the DOM:

    "creation_time":1788012882
    "publish_time":1788012882

On a permalink each of these appears exactly once and the two agree. A page
listing, by contrast, carries the timestamps of many posts — so when more than
one distinct value is present the extractor raises rather than guessing.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import ParseResult, parse_qs

from .. import browser, fetch
from .base import ImageRef, Match, Platform, PlatformError, Timing

_ID = r"[0-9]{6,25}"
_TOKEN = r"[A-Za-z0-9]{6,120}"
_PATHS = (
    re.compile(rf"^/(?:[^/]+)/posts/(?P<id>{_TOKEN})"),
    re.compile(rf"^/(?:[^/]+)/videos/(?:[^/]+/)?(?P<id>{_ID})"),
    re.compile(rf"^/(?:reel|videos|photos)/(?P<id>{_ID})"),
    re.compile(rf"^/share/[pvr]/(?P<id>{_TOKEN})"),
    re.compile(rf"^/(?P<id>{_ID})$"),
)
_QUERY_KEYS = ("story_fbid", "fbid", "v", "id")

_TIME_FIELDS = (
    ("creation_time", re.compile(r'"creation_time":(\d{10})')),
    ("publish_time", re.compile(r'"publish_time":(\d{10})')),
    ("publish_time_escaped", re.compile(r'\\"publish_time\\":(\d{10})')),
    ("data-utime", re.compile(r'data-utime="(\d{10})"')),
)
_IMG = re.compile(r'<img[^>]+src="(https://[^"]+)"')
_CDN = re.compile(r"fbcdn\.net|cdninstagram\.com")
_JUNK = re.compile(r"/hads-ak|/rsrc\.php|static\.xx\.fbcdn\.net")
_LOGIN = re.compile(r"login_form|You must log in", re.I)
_GONE = re.compile(r"content isn.t available|page isn.t available", re.I)

_FLOOR = datetime(2004, 2, 1, tzinfo=timezone.utc)      # Facebook's launch


def _plausible(ts: int) -> bool:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return _FLOOR <= dt <= datetime.now(timezone.utc) + timedelta(days=1)


def extract_time(dom: str, expect_id: str | None = None) -> tuple[datetime, str]:
    if expect_id and expect_id.isdigit() and f'"{expect_id}"' not in dom:
        raise PlatformError(
            f"Post {expect_id} was not present on the page. The link may be "
            f"wrong, or the post may no longer be public.",
            platform="facebook", reason="not_visible")

    for name, pattern in _TIME_FIELDS:
        found = {int(v) for v in pattern.findall(dom)}
        found = {v for v in found if _plausible(v)}
        if not found:
            continue
        if len(found) > 1:
            raise PlatformError(
                f"The DOM contains {len(found)} distinct {name} values, so the "
                f"one belonging to this post cannot be identified with certainty",
                platform="facebook", reason="upstream_error")
        return datetime.fromtimestamp(found.pop(), tz=timezone.utc), name

    raise PlatformError(
        "No timestamp was found on the page; Facebook may have changed its markup",
        platform="facebook", reason="upstream_error")


def pick_media(html: str) -> list[ImageRef]:
    out: list[ImageRef] = []
    seen: set[str] = set()
    for url in fetch.og_images(html):
        key = url.split("?")[0]
        if key not in seen:
            seen.add(key)
            out.append(ImageRef(url, "post", "main post image"))
    for url in _IMG.findall(html):
        key = url.split("?")[0]
        if key in seen or not _CDN.search(url) or _JUNK.search(url):
            continue
        seen.add(key)
        out.append(ImageRef(url, "page", "found on the post page"))
    return out


class Facebook(Platform):
    id = "facebook"
    label = "Facebook"
    hosts = frozenset({"facebook.com", "m.facebook.com", "fb.com", "fb.watch",
                       "web.facebook.com", "mbasic.facebook.com"})
    sample_url = "https://www.facebook.com/NASA/posts/1615702003258503"
    time_method = "headless-page"
    time_note = "creation_time from the embedded JSON of the rendered page"
    image_note = "CDN images from the same render, with UI assets filtered out"
    needs_browser = True

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        path = parts.path.rstrip("/") or "/"
        if host == "fb.watch":
            token = path.lstrip("/")
            return Match(token, url, url) if token else None
        for pattern in _PATHS:
            m = pattern.match(path)
            if m:
                return Match(m["id"], url, url)
        query = parse_qs(parts.query)
        for key in _QUERY_KEYS:
            value = (query.get(key) or [""])[0]
            if re.fullmatch(_ID, value):
                return Match(value, url, url)
        return None

    async def load(self, match: Match) -> dict:
        try:
            dom = await browser.render(match.render_url)
        except browser.BrowserNotAvailableError as e:
            raise PlatformError(str(e), platform=self.id,
                                reason="not_configured") from e
        except browser.BrowserError as e:
            raise PlatformError(str(e), platform=self.id,
                                reason="upstream_error") from e

        if _GONE.search(dom) and not any(p.search(dom) for _, p in _TIME_FIELDS):
            raise PlatformError(
                "The post is not available; it may have been deleted or made private",
                platform=self.id, reason="not_visible")
        if _LOGIN.search(dom) and not any(p.search(dom) for _, p in _TIME_FIELDS):
            raise PlatformError(
                "Facebook asked for a login — this post is not public",
                platform=self.id, reason="not_visible")
        return {"dom": dom}

    async def published_at(self, match: Match, ctx: dict) -> Timing:
        dt, _field = extract_time(ctx["dom"], match.post_id)
        return Timing(dt, self.time_method, "second")

    async def images(self, match: Match, ctx: dict) -> list[ImageRef]:
        found = pick_media(ctx["dom"])
        if not found:
            raise PlatformError("No image was found on this post",
                                platform=self.id, reason="no_media")
        return found
