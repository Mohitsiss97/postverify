"""Reading a post's publish time from its URL, and matching an image against it.

This is an API-only service, so it has no sessions, no temporary directories
and no `/media` route. Images from the post are held **in memory only**: they
are downloaded, compared, and discarded when the request ends. Nothing is
written to disk, so there is nothing to clean up afterwards.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import fetch
from . import platforms as reg
from .compare import Comparison, compare, decode
from .platforms import ImageRef, PlatformError

# Upper bound on how many images from one post are examined.
MAX_IMAGES = 12
_DOWNLOAD_CONCURRENCY = 6
_RANK = {"identical": 3, "same": 2, "likely": 1, "different": 0}


@dataclass
class PostTime:
    published_at: datetime          # always UTC
    published_at_local: str | None
    timezone: str | None
    age_seconds: int
    age_human: str
    method: str                     # id-embedded | public-page | headless-page | api
    precision: str                  # millisecond | second


@dataclass
class Candidate:
    tier: str                       # post | page
    verdict: str = "error"
    score: int = 0
    phash_distance: int | None = None
    orb_inliers: int = 0
    note: str = ""
    error: str | None = None

    @property
    def rank(self) -> int:
        return _RANK.get(self.verdict, -1)


@dataclass
class ImageMatch:
    present: bool
    verdict: str
    score: int
    images_checked: int
    matched: Candidate | None = None


@dataclass
class Result:
    platform: str
    platform_label: str
    post_id: str
    canonical_url: str
    time: PostTime | None = None
    time_error: str | None = None
    image: ImageMatch | None = None
    image_error: str | None = None
    _errors: list[PlatformError] = field(default_factory=list, repr=False)


# --- time ---------------------------------------------------------------

def _age_human(seconds: int) -> str:
    if seconds < 0:
        return "in the future (clock skew?)"
    for name, size in (("year", 31_536_000), ("month", 2_592_000), ("day", 86_400),
                       ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {name}{'s' if count != 1 else ''} old"
    return f"{seconds} second{'s' if seconds != 1 else ''} old"


def _build_time(timing, tz: str | None) -> PostTime:
    dt = timing.published_at
    local, tzname = None, None
    if tz:
        try:
            local = dt.astimezone(ZoneInfo(tz)).isoformat()
            tzname = tz
        except Exception:
            # An unknown timezone must not cost the caller the whole answer.
            local, tzname = None, None
    age = int((datetime.now(timezone.utc) - dt).total_seconds())
    return PostTime(dt, local, tzname, age, _age_human(age),
                    timing.method, timing.precision)


# --- images -------------------------------------------------------------

def _collapse_groups(images: list[ImageRef]) -> list[list[ImageRef]]:
    """A group is one image at several resolutions; the first to download wins."""
    groups: list[list[ImageRef]] = []
    by_key: dict[str, list[ImageRef]] = {}
    for ref in images:
        if not ref.group:
            groups.append([ref])
        else:
            by_key.setdefault(ref.group, []).append(ref)
    groups.extend(by_key.values())
    return groups


async def _download_group(group: list[ImageRef]) -> tuple[ImageRef, bytes | None]:
    for ref in group:
        try:
            return ref, await fetch.get_image(ref.url)
        except fetch.FetchError:
            continue
    return group[0], None


async def _compare_all(uploaded: bytes, groups: list[list[ImageRef]]) -> list[Candidate]:
    gate = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)

    async def one(group: list[ImageRef]) -> Candidate:
        async with gate:
            ref, data = await _download_group(group)
        cand = Candidate(tier=ref.tier)
        if data is None:
            cand.error = "download failed"
            return cand
        try:
            # OpenCV is blocking; keep it off the event loop.
            result: Comparison = await asyncio.to_thread(compare, uploaded, data)
        except Exception as e:
            cand.error = str(e)
            return cand
        cand.verdict = result.verdict
        cand.score = result.score
        cand.phash_distance = result.phash_distance
        cand.orb_inliers = result.orb_inliers
        cand.note = result.note
        return cand

    return list(await asyncio.gather(*(one(g) for g in groups)))


# --- the single entry point ---------------------------------------------

async def resolve(url: str, *, tz: str | None = None,
                  uploaded: bytes | None = None) -> Result:
    """Detect the platform, read the publish time, and compare an image if given.

    The expensive step — rendering or fetching the page — happens exactly once;
    both the timestamp and the images are derived from that single result.

    Time and image are evaluated independently, so a failure in one does not
    stop the other. A partial answer is more useful than no answer.
    """
    platform, m = reg.detect(url)
    if platform.id not in {p.id for p in reg.enabled()}:
        raise PlatformError(f"{platform.label} is not enabled in this deployment",
                            platform=platform.id, reason="disabled")

    if uploaded is not None:
        # Fail fast on a corrupt upload, before spending any download time.
        decode(uploaded)

    result = Result(platform=platform.id, platform_label=platform.label,
                    post_id=m.post_id, canonical_url=m.canonical_url)

    ctx = await platform.load(m)

    try:
        result.time = _build_time(await platform.published_at(m, ctx), tz)
    except PlatformError as e:
        result.time_error = str(e)
        result._errors.append(e)

    if uploaded is not None:
        try:
            groups = _collapse_groups(await platform.images(m, ctx))[:MAX_IMAGES]
            cands = await _compare_all(uploaded, groups)
            usable = [c for c in cands if c.error is None]
            if not usable:
                result.image_error = "none of the post's images could be downloaded"
            else:
                best = max(usable, key=lambda c: (c.rank, c.tier == "post", c.score))
                result.image = ImageMatch(
                    present=best.rank > 0,
                    verdict=best.verdict,
                    score=best.score,
                    images_checked=len(usable),
                    matched=best if best.rank > 0 else None,
                )
        except PlatformError as e:
            result.image_error = str(e)
            result._errors.append(e)

    # If nothing at all came back, re-raise the original exception so its reason
    # (invalid_id, not_visible, ...) survives. Collapsing it into a generic 502
    # would discard the only useful information the caller has.
    if result.time is None and result.image is None:
        raise result._errors[0] if result._errors else PlatformError(
            "nothing could be resolved", platform=platform.id, reason="upstream_error")

    return result
