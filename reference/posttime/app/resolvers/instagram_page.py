"""Instagram ka public post page — headless browser se.

Instagram ka HTML server se khali aata hai; post ka time browser me JS chalne ke
baad DOM me aata hai:

    <time datetime="2026-08-25T17:29:13.000Z" title="Aug 25, 2026">

DOM me pehla <time> post ka apna hota hai, baaki comments aur related posts ke.
Ye 18 asli posts pe verify kiya gaya: har shortcode ka media ID decode karke
posts ko ID order me lagaya, aur extracted timestamps 18/18 ascending nikle.

Ye ek heuristic hai, contract nahi. Instagram DOM badal de to ye toot jayega —
isliye result ka method "headless-page" hota hai, taaki pata rahe ki jawab kahan
se aaya. Token configured ho to Graph API pehle chalti hai.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import browser

_TIME = re.compile(r'<time[^>]*\sdatetime="([^"]+)"')
_LOGIN = re.compile(r"login_form|Log in to see|accounts/login", re.I)
_GONE = re.compile(r"Sorry, this page isn\'t available|Post not available", re.I)

# Instagram October 2010 me launch hua — usse pehle ka koi post ho hi nahi sakta.
_FLOOR = datetime(2010, 10, 1, tzinfo=timezone.utc)


class PageError(RuntimeError):
    pass


class NotVisibleError(PageError):
    """Post private hai, delete ho gaya, ya login maang raha hai."""


def extract(dom: str) -> datetime:
    """Rendered DOM se post ka timestamp nikalo."""
    m = _TIME.search(dom)
    if not m:
        if _GONE.search(dom):
            raise NotVisibleError("Post available nahi hai — delete ya private ho sakta hai")
        if _LOGIN.search(dom):
            raise NotVisibleError(
                "Instagram ne login maanga — ye post public nahi hai, ya IG ne "
                "is IP se anonymous access rok diya hai")
        raise PageError(
            "Page pe koi <time> nahi mila. Instagram ne DOM badal diya ho sakta hai — "
            "IG_ACCESS_TOKEN set karke Graph API waala raasta use kijiye.")

    raw = m.group(1)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as e:
        raise PageError(f"Timestamp parse nahi hua: {raw!r}") from e

    now = datetime.now(timezone.utc)
    if not _FLOOR <= dt <= now + timedelta(days=1):
        raise PageError(
            f"Nikla hua timestamp bharosemand nahi lagta ({dt.isoformat()}) — "
            f"shayad ye post ka time hai hi nahi")
    return dt


async def published_at(shortcode: str) -> datetime:
    url = f"https://www.instagram.com/p/{shortcode}/"
    try:
        dom = await browser.render(url)
    except browser.BrowserNotAvailableError:
        raise
    except browser.RenderTimeoutError as e:
        raise PageError(str(e)) from e
    except browser.BrowserError as e:
        raise PageError(str(e)) from e
    return extract(dom)
