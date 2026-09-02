"""Facebook ka public post page — headless browser se.

Facebook ka HTML bhi Instagram jaisa hai: server se khali shell aata hai. Par
browser me render karo to post ka time embedded JSON me saaf milta hai:

    "creation_time":1788012882
    "publish_time":1788012882

Ye probe karke confirm kiya. Ek post permalink pe:
  - creation_time aur publish_time dono **exactly ek baar** aate hain, aur equal hote hain
  - requested post_id DOM me 4 baar milta hai
  - teen alag renders pe jawab bilkul same rehta hai

Iske ulat page listing (facebook.com/NASA) pe kai posts ke timestamps hote hain —
isliye service sirf post permalink render karti hai, page nahi. Aur agar DOM me
ek se zyada alag-alag creation_time mile to hum guess nahi karte, error dete hain.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import browser

# Order matters — sabse specific pehle. (Ye list user ke script se li gayi hai,
# guards add karke.)
_PATTERNS = (
    ("creation_time", re.compile(r'"creation_time":(\d{10})')),
    ("publish_time", re.compile(r'"publish_time":(\d{10})')),
    ("publish_time_escaped", re.compile(r'\\"publish_time\\":(\d{10})')),
    ("data-utime", re.compile(r'data-utime="(\d{10})"')),
)

_LOGIN = re.compile(r"login_form|You must log in|Log in to continue", re.I)
_GONE = re.compile(r"content isn.t available|page isn.t available|Content Not Found", re.I)

# Facebook Feb 2004 me launch hua.
_FLOOR = datetime(2004, 2, 1, tzinfo=timezone.utc)


class PageError(RuntimeError):
    pass


class NotVisibleError(PageError):
    """Post private hai, delete ho gaya, ya login maang raha hai."""


class AmbiguousError(PageError):
    """DOM me ek se zyada post ke timestamps hain — guess nahi karenge."""


def _valid(ts: int) -> bool:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return _FLOOR <= dt <= datetime.now(timezone.utc) + timedelta(days=1)


def extract(dom: str, *, expect_id: str | None = None) -> tuple[datetime, str]:
    """Rendered DOM se post ka creation time nikalo.

    expect_id diya ho to pehle confirm karo ki hum sahi post pe hain — warna
    kisi related post ka time uthane ka risk rehta hai.
    """
    if expect_id and expect_id.isdigit() and f'"{expect_id}"' not in dom:
        if _LOGIN.search(dom):
            raise NotVisibleError(
                "Facebook ne login maanga — ye post public nahi hai, ya FB ne is IP "
                "se anonymous access rok diya hai")
        if _GONE.search(dom):
            raise NotVisibleError("Post available nahi hai — delete ya private ho sakta hai")
        raise NotVisibleError(
            f"Page pe post {expect_id} mila hi nahi — link galat ho sakta hai, ya "
            f"post ab public nahi hai")

    for name, pattern in _PATTERNS:
        found = {int(v) for v in pattern.findall(dom)}
        found = {v for v in found if _valid(v)}
        if not found:
            continue
        if len(found) > 1:
            raise AmbiguousError(
                f"DOM me {len(found)} alag {name} values hain — kaunsa is post ka hai, "
                f"pakka nahi kaha ja sakta")
        ts = found.pop()
        return datetime.fromtimestamp(ts, tz=timezone.utc), name

    if _LOGIN.search(dom):
        raise NotVisibleError(
            "Facebook ne login maanga — ye post public nahi hai, ya FB ne is IP se "
            "anonymous access rok diya hai")
    if _GONE.search(dom):
        raise NotVisibleError("Post available nahi hai — delete ya private ho sakta hai")
    raise PageError(
        "Page pe koi timestamp nahi mila. Facebook ne markup badal diya ho sakta hai — "
        "FB_ACCESS_TOKEN set karke Graph API waala raasta use kijiye.")


async def published_at(render_url: str, *, expect_id: str | None = None) -> tuple[datetime, str]:
    try:
        dom = await browser.render(render_url)
    except browser.BrowserNotAvailableError:
        raise
    except browser.RenderTimeoutError as e:
        raise PageError(str(e)) from e
    except browser.BrowserError as e:
        raise PageError(str(e)) from e
    return extract(dom, expect_id=expect_id)
