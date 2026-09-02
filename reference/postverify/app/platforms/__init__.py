"""Platform registry.

Yahan koi picker nahi hai — user sirf URL deta hai aur platform khud pehchana
jaata hai. Isliye detect() hi asli entry point hai.

    PLATFORMS=x,youtube uvicorn app.main:app    # sirf ye do, browser chahiye hi nahi
"""
from __future__ import annotations

import os
import re
from urllib.parse import ParseResult, urlparse

from .base import (
    ImageRef,
    Match,
    Platform,
    PlatformError,
    Timing,
    UnsupportedURLError,
)
from .facebook import Facebook
from .instagram import Instagram
from .linkedin import LinkedIn
from .x import X
from .youtube import YouTube

__all__ = [
    "ImageRef", "Match", "Platform", "PlatformError", "Timing",
    "UnsupportedURLError", "CATALOG", "catalog", "enabled", "get", "detect", "split",
]

CATALOG: tuple[Platform, ...] = (X(), Instagram(), Facebook(), LinkedIn(), YouTube())
_BY_ID = {p.id: p for p in CATALOG}


def _enabled_ids() -> list[str]:
    raw = (os.getenv("PLATFORMS") or "").strip()
    if not raw:
        return [p.id for p in CATALOG]
    wanted = [w.strip().lower() for w in raw.split(",") if w.strip()]
    unknown = [w for w in wanted if w not in _BY_ID]
    if unknown:
        raise RuntimeError(
            f"PLATFORMS env me unknown platform: {', '.join(unknown)}. "
            f"Valid: {', '.join(_BY_ID)}")
    return wanted


def catalog() -> tuple[Platform, ...]:
    return CATALOG


def enabled() -> list[Platform]:
    ids = set(_enabled_ids())
    return [p for p in CATALOG if p.id in ids]


def get(platform_id: str) -> Platform:
    pid = (platform_id or "").lower()
    for p in enabled():
        if p.id == pid:
            return p
    raise KeyError(pid)


def split(url: str) -> tuple[str, ParseResult, str]:
    url = (url or "").strip()
    if not url:
        raise UnsupportedURLError("URL khali hai")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return url, parts, host


def detect(url: str) -> tuple[Platform, Match]:
    """URL se platform pehchano.

    Poora catalog dekha jaata hai, sirf enabled nahi — taaki disabled platform ka
    link daalne pe "ye X ka link hai par X band hai" bata sakein.
    """
    normalized, parts, host = split(url)
    for platform in CATALOG:
        m = platform.match(normalized, parts, host)
        if m:
            return platform, m
    raise UnsupportedURLError(
        f"Ye URL kisi supported platform se match nahi hua: {normalized}. "
        f"Support hain: {', '.join(p.label for p in CATALOG)}")
