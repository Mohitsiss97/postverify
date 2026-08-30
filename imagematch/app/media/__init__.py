"""Source registry.

Naya platform add karna = ek file banao, yahan CATALOG me daalo. Route,
/platforms, aur UI ka picker — teeno apne aap update ho jaate hain.

    PLATFORMS=x,youtube uvicorn app.main:app    # sirf ye do, browser chahiye hi nahi
"""
from __future__ import annotations

import os
import re
from urllib.parse import ParseResult, urlparse

from .base import (
    ExtractionError,
    ImageRef,
    Match,
    Source,
    UnsupportedURLError,
    WrongPlatformError,
)
from .facebook import Facebook
from .instagram import Instagram
from .linkedin import LinkedIn
from .x import X
from .youtube import YouTube

__all__ = [
    "ExtractionError", "ImageRef", "Match", "Source",
    "UnsupportedURLError", "WrongPlatformError",
    "CATALOG", "catalog", "enabled", "get", "detect", "match_on", "split",
]

CATALOG: tuple[Source, ...] = (X(), Instagram(), Facebook(), LinkedIn(), YouTube())
_BY_ID = {s.id: s for s in CATALOG}


def _enabled_ids() -> list[str]:
    raw = (os.getenv("PLATFORMS") or "").strip()
    if not raw:
        return [s.id for s in CATALOG]
    wanted = [w.strip().lower() for w in raw.split(",") if w.strip()]
    unknown = [w for w in wanted if w not in _BY_ID]
    if unknown:
        raise RuntimeError(
            f"PLATFORMS env me unknown platform: {', '.join(unknown)}. "
            f"Valid: {', '.join(_BY_ID)}")
    return wanted


def catalog() -> tuple[Source, ...]:
    return CATALOG


def enabled() -> list[Source]:
    ids = set(_enabled_ids())
    return [s for s in CATALOG if s.id in ids]


def get(platform_id: str) -> Source:
    pid = (platform_id or "").lower()
    for s in enabled():
        if s.id == pid:
            return s
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


def match_on(source: Source, url: str) -> Match | None:
    return source.match(*split(url))


def detect(url: str) -> tuple[Source, Match]:
    """Poore catalog me se URL ka platform dhoondo."""
    normalized, parts, host = split(url)
    for source in CATALOG:
        m = source.match(normalized, parts, host)
        if m:
            return source, m
    raise UnsupportedURLError(
        f"Ye URL kisi supported platform se match nahi hua: {normalized}")
