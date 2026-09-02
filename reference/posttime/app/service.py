"""Orchestration: URL -> PostTiming.

Do entry points:
  resolve_with(platform, url)  — user ne platform chuna hai (per-platform service)
  resolve(url)                 — platform khud detect karo (convenience)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import platforms as reg
from .platforms import (
    Platform,
    ResolutionError,
    UnsupportedURLError,
    WrongPlatformError,
)


@dataclass
class PostTiming:
    platform: str
    platform_label: str
    post_id: str
    canonical_url: str
    published_at: datetime          # hamesha UTC
    published_at_local: str | None
    timezone: str | None
    age_seconds: int
    age_human: str
    method: str
    precision: str

    def dict(self) -> dict:
        return asdict(self)


def _age_human(seconds: int) -> str:
    if seconds < 0:
        return "future me (clock skew?)"
    units = (("saal", 31_536_000), ("mahine", 2_592_000), ("din", 86_400),
             ("ghante", 3600), ("minute", 60))
    for name, size in units:
        if seconds >= size:
            return f"{seconds // size} {name} purana"
    return f"{seconds} second purana"


def _build(platform: Platform, match, timing, tz: str | None) -> PostTiming:
    dt = timing.published_at
    local, tzname = None, None
    if tz:
        try:
            local = dt.astimezone(ZoneInfo(tz)).isoformat()
            tzname = tz
        except Exception:
            local, tzname = None, None

    age = int((datetime.now(timezone.utc) - dt).total_seconds())
    return PostTiming(
        platform=platform.id,
        platform_label=platform.label,
        post_id=match.post_id,
        canonical_url=match.canonical_url,
        published_at=dt,
        published_at_local=local,
        timezone=tzname,
        age_seconds=age,
        age_human=_age_human(age),
        method=timing.method,
        precision=timing.precision,
    )


async def resolve_with(platform: Platform, url: str, *, tz: str | None = None) -> PostTiming:
    """User ne platform chuna hai — us platform ki service chalao.

    Agar URL kisi doosre platform ka nikla to chup-chaap resolve NAHI karte;
    WrongPlatformError uthate hain. User ne khud platform chuna hai, to galti
    uske saamne aani chahiye.
    """
    match = reg.match_on(platform, url)          # UnsupportedURLError propagate ho sakta hai
    if match is None:
        try:
            other, _ = reg.detect(url)
        except UnsupportedURLError:
            raise UnsupportedURLError(
                f"Ye {platform.label} ka valid post URL nahi lagta. "
                f"Example: {platform.sample_url}") from None
        raise WrongPlatformError(
            expected=platform.id, actual=other.id, url=url,
            expected_label=platform.label, actual_label=other.label)

    timing = await platform.timing(match.post_id, match.extra)
    return _build(platform, match, timing, tz)


async def resolve(url: str, *, tz: str | None = None) -> PostTiming:
    """Platform khud detect karke resolve karo."""
    platform, match = reg.detect(url)            # UnsupportedURLError propagate
    if platform.id not in {p.id for p in reg.enabled()}:
        raise ResolutionError(
            f"{platform.label} is deployment me enabled nahi hai",
            platform=platform.id, reason="disabled")
    timing = await platform.timing(match.post_id, match.extra)
    return _build(platform, match, timing, tz)
