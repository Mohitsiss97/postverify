"""Platform registry.

There is no platform picker in this API: the caller supplies only a URL and the
platform is identified from it, which makes detect() the real entry point.

The PLATFORMS environment variable narrows the registry, which is how a
deployment that only needs the offline platforms avoids requiring a browser at
all:

    PLATFORMS=x,youtube uvicorn app.main:app
"""
from __future__ import annotations

import re
from urllib.parse import ParseResult, urlparse

from .. import config
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
    raw = config.platforms_raw()
    if not raw:
        return [p.id for p in CATALOG]
    wanted = [w.strip().lower() for w in raw.split(",") if w.strip()]
    unknown = [w for w in wanted if w not in _BY_ID]
    if unknown:
        raise RuntimeError(
            f"Unknown platform in the PLATFORMS environment variable: "
            f"{', '.join(unknown)}. Valid values: {', '.join(_BY_ID)}")
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
        raise UnsupportedURLError("The URL is empty")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return url, parts, host


def detect(url: str) -> tuple[Platform, Match]:
    """Identify the platform from the URL.

    The whole catalogue is consulted, not just the enabled subset, so that a
    link to a disabled platform can be reported as "this is an X link, but X is
    turned off" rather than as an unrecognised URL.
    """
    normalized, parts, host = split(url)
    for platform in CATALOG:
        m = platform.match(normalized, parts, host)
        if m:
            return platform, m
    raise UnsupportedURLError(
        f"This URL does not match any supported platform: {normalized}. "
        f"Supported platforms: {', '.join(p.label for p in CATALOG)}")
