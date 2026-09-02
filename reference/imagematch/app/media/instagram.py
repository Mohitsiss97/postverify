"""Instagram — images rendered DOM se.

Instagram ka HTML server se khali aata hai, isliye browser chahiye. Rendered DOM
me CDN URLs ka prefix batata hai wo kis type ki image hai:

    t51.82787-15   post ki media       <- ye chahiye
    t51.82787-19   profile picture     <- nahi chahiye
    t51.2885-19    profile picture (purana format)
    t59.2708-21    doosre assets

og:image hamesha isi post ki hoti hai. Baaki -15 images carousel ki doosri slide
ho sakti hain, ya "more posts" section ki kisi aur post ki — isliye unhe "page"
tier me rakhte hain aur result me alag se batate hain.
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult

from .. import browser, fetch
from .base import ExtractionError, ImageRef, Match, Source

_PATH = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/(?P<code>[A-Za-z0-9_-]+)")
_IMG = re.compile(r'<img[^>]+src="(https://[^"]+)"')
_MEDIA_PREFIX = re.compile(r"/v/t\d+\.\d+-15/")
_FILE_ID = re.compile(r"/(\d+)_(\d+)_(\d+)_n\.")
_WIDTH = re.compile(r"[?&]stp=[^&]*?[pe](\d{3,4})x\d{3,4}")


def _file_key(url: str) -> str:
    m = _FILE_ID.search(url)
    return m.group(1) if m else url.split("?")[0]


def _width(url: str) -> int:
    m = _WIDTH.search(url)
    return int(m.group(1)) if m else 0


def pick_media(html: str) -> list[ImageRef]:
    """DOM se post ki images chuno, sabse bade version me, bina duplicate."""
    og = fetch.og_images(html)
    in_page = [u for u in _IMG.findall(html) if _MEDIA_PREFIX.search(u)]

    # Ek hi image kai sizes me aati hai (640, 1080...). File id pe group karke
    # sabse bada version rakhte hain — chhoti image se compare karna kamzor hai.
    # og waali bhi isi group me daalte hain, warna page ki chhoti copy jeet jaati hai.
    best: dict[str, str] = {}
    for url in og + in_page:
        key = _file_key(url)
        if key not in best or _width(url) > _width(best[key]):
            best[key] = url

    og_keys = []
    for url in og:
        key = _file_key(url)
        if key not in og_keys:
            og_keys.append(key)

    out = [ImageRef(best[key], "post", "post ki main image") for key in og_keys]
    out += [ImageRef(url, "page", "post page pe mili")
            for key, url in best.items() if key not in og_keys]
    return out


class Instagram(Source):
    id = "instagram"
    label = "Instagram"
    hosts = frozenset({"instagram.com", "instagr.am", "ddinstagram.com"})
    sample_url = "https://www.instagram.com/p/DceLPdrCR3L/"
    how = ("Post page browser me render karke uski CDN images nikaali jaati hain. "
           "~6 second lagta hai.")
    needs_browser = True

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        if host not in self.hosts:
            return None
        m = _PATH.match(parts.path.rstrip("/") or "/")
        if not m:
            return None
        canonical = f"https://www.instagram.com/p/{m['code']}/"
        return Match(m["code"], canonical, canonical)

    async def images(self, match: Match) -> list[ImageRef]:
        try:
            dom = await browser.render(match.render_url)
        except browser.BrowserNotAvailableError as e:
            raise ExtractionError(str(e), platform=self.id, reason="not_configured") from e
        except browser.BrowserError as e:
            raise ExtractionError(str(e), platform=self.id, reason="upstream_error") from e

        found = pick_media(dom)
        if not found:
            if re.search(r"login_form|Log in to see", dom, re.I):
                raise ExtractionError(
                    "Instagram ne login maanga — ye post public nahi hai",
                    platform=self.id, reason="not_visible")
            raise ExtractionError(
                "Is post pe koi image nahi mili", platform=self.id, reason="no_media")
        return found
