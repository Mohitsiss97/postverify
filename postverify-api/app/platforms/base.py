"""Ek platform, teen kaam.

PostTime aur ImageMatch me ye alag-alag the. Yahan ek hi jagah hain, kyunki
dono ka URL parsing bilkul same tha aur dono ek hi page ko render karte the —
alag rakhne ka matlab tha ek hi post ke liye browser do baar chalana.

Har platform ye batata hai:
    match()         ye URL mera hai? (aur post ka id kya hai)
    published_at()  ye post kab bana?
    images()        is post pe kaunsi images hain?
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import ParseResult


class UnsupportedURLError(ValueError):
    """URL kisi bhi known platform se match nahi hua."""


class PlatformError(RuntimeError):
    """Platform pehchana gaya, par kaam poora nahi hua."""

    def __init__(self, message: str, *, platform: str, reason: str):
        super().__init__(message)
        self.platform = platform
        self.reason = reason


@dataclass(frozen=True)
class Match:
    post_id: str
    canonical_url: str
    render_url: str
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Timing:
    published_at: datetime      # hamesha UTC
    method: str                 # id-embedded | public-page | headless-page | api
    precision: str              # millisecond | second


@dataclass(frozen=True)
class ImageRef:
    """Post pe mili ek image.

    tier:
      "post"  — og:image se, yaani pakka isi post ki
      "page"  — post ke page pe mili; carousel slide ho sakti hai ya related post ki
    """
    url: str
    tier: str = "post"
    label: str = ""
    group: str = ""             # ek hi image ke alag resolutions


class Platform:
    id: str = ""
    label: str = ""
    hosts: frozenset[str] = frozenset()
    sample_url: str = ""

    time_method: str = ""
    time_note: str = ""
    image_note: str = ""
    needs_browser: bool = False
    optional_env: str | None = None      # ho to behtar, par zaroori nahi

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        raise NotImplementedError

    async def load(self, match: Match) -> dict:
        """Ek baar ka mehenga kaam — page render ya fetch.

        Time aur images dono isi ek nateeje se nikalte hain. Isliye ek post pe
        browser sirf ek baar chalta hai, do baar nahi.
        """
        return {}

    async def published_at(self, match: Match, ctx: dict) -> Timing:
        raise NotImplementedError

    async def images(self, match: Match, ctx: dict) -> list[ImageRef]:
        raise NotImplementedError

    # -- config ----------------------------------------------------------

    def configured(self) -> bool:
        return self.optional_env is not None and bool(os.getenv(self.optional_env))

    def ready(self) -> bool:
        if not self.needs_browser:
            return True
        from .. import browser
        return browser.available()

    def descriptor(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "hosts": sorted(self.hosts),      # UI live chip dikhane ke liye
            "sample_url": self.sample_url,
            "time_method": self.time_method,
            "time_note": self.time_note,
            "image_note": self.image_note,
            "needs_browser": self.needs_browser,
            "ready": self.ready(),
        }
