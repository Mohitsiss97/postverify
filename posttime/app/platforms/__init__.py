"""Platform registry.

Naya platform add karna = ek file banao, yahan CATALOG me add karo. Bas.
Router, /platforms endpoint, aur UI ka picker — sab automatically update ho jaate hain.

Ek deployment sirf kuch platforms bhi chala sakti hai:

    PLATFORMS=x,linkedin uvicorn app.main:app     # offline-only, network ki zaroorat nahi
    PLATFORMS=youtube    uvicorn app.main:app     # sirf API wala

Isse secrets aur scaling dono alag-alag rakhe ja sakte hain.
"""
from __future__ import annotations

import os
import re
from urllib.parse import ParseResult, urlparse

from .base import (
    Match,
    Platform,
    ResolutionError,
    Timing,
    UnsupportedURLError,
    WrongPlatformError,
)
from .facebook import Facebook
from .instagram import Instagram
from .linkedin import LinkedIn
from .x import X
from .youtube import YouTube

__all__ = [
    "Match", "Platform", "ResolutionError", "Timing",
    "UnsupportedURLError", "WrongPlatformError",
    "CATALOG", "catalog", "enabled", "get", "detect", "match_on", "split",
]

# Sab known platforms — chahe is deployment me enabled hon ya nahi.
CATALOG: tuple[Platform, ...] = (X(), LinkedIn(), YouTube(), Instagram(), Facebook())

_BY_ID = {p.id: p for p in CATALOG}


def _enabled_ids() -> list[str]:
    raw = (os.getenv("PLATFORMS") or "").strip()
    if not raw:
        return [p.id for p in CATALOG]
    wanted = [s.strip().lower() for s in raw.split(",") if s.strip()]
    unknown = [w for w in wanted if w not in _BY_ID]
    if unknown:
        raise RuntimeError(
            f"PLATFORMS env me unknown platform: {', '.join(unknown)}. "
            f"Valid: {', '.join(_BY_ID)}")
    return wanted


def catalog() -> tuple[Platform, ...]:
    return CATALOG


def enabled() -> list[Platform]:
    """Is deployment me jo services mounted hain."""
    ids = set(_enabled_ids())
    return [p for p in CATALOG if p.id in ids]


def get(platform_id: str) -> Platform:
    """Enabled platform lao. Warna KeyError."""
    pid = (platform_id or "").lower()
    for p in enabled():
        if p.id == pid:
            return p
    raise KeyError(pid)


# --- URL handling -------------------------------------------------------

def split(url: str) -> tuple[str, ParseResult, str]:
    """URL ko normalize karke (url, parts, host) do."""
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


def match_on(platform: Platform, url: str) -> Match | None:
    """Ek hi platform pe try karo."""
    return platform.match(*split(url))


def detect(url: str) -> tuple[Platform, Match]:
    """Poore catalog me se URL ka platform dhoondo.

    Catalog use hota hai, enabled list nahi — taaki disabled platform ka link
    daalne pe "ye X ka link hai par X service band hai" bata sakein.
    """
    normalized, parts, host = split(url)
    for p in CATALOG:
        m = p.match(normalized, parts, host)
        if m:
            return p, m
    raise UnsupportedURLError(f"Ye URL kisi supported platform se match nahi hua: {normalized}")
