"""URL se time nikalna, aur image ko post se milana.

Ye API-only service hai, isliye yahan koi session, koi temp folder, koi
`/media` route nahi hai. Post ki images sirf **memory me** aati hain, compare
hoti hain, aur request khatam hote hi chali jaati hain — disk pe kuch likha hi
nahi jaata, to mitane ko bhi kuch nahi bachta.
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

# Ek post pe itni se zyada images check nahi karte — bachav.
MAX_IMAGES = 12
_DOWNLOAD_CONCURRENCY = 6
_RANK = {"identical": 3, "same": 2, "likely": 1, "different": 0}


@dataclass
class PostTime:
    published_at: datetime          # hamesha UTC
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
        return "future me (clock skew?)"
    for name, size in (("saal", 31_536_000), ("mahine", 2_592_000), ("din", 86_400),
                       ("ghante", 3600), ("minute", 60)):
        if seconds >= size:
            return f"{seconds // size} {name} purana"
    return f"{seconds} second purana"


def _build_time(timing, tz: str | None) -> PostTime:
    dt = timing.published_at
    local, tzname = None, None
    if tz:
        try:
            local = dt.astimezone(ZoneInfo(tz)).isoformat()
            tzname = tz
        except Exception:
            local, tzname = None, None      # galat timezone se poora jawab na ruke
    age = int((datetime.now(timezone.utc) - dt).total_seconds())
    return PostTime(dt, local, tzname, age, _age_human(age),
                    timing.method, timing.precision)


# --- images -------------------------------------------------------------

def _collapse_groups(images: list[ImageRef]) -> list[list[ImageRef]]:
    """Ek group = ek hi image ke alag resolutions; pehli jo download ho jaye wahi."""
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
            cand.error = "download nahi hui"
            return cand
        try:
            # OpenCV blocking hai — event loop ko rokne se bachao
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


# --- ek hi entry point --------------------------------------------------

async def resolve(url: str, *, tz: str | None = None,
                  uploaded: bytes | None = None) -> Result:
    """Platform detect karo, time nikalo, aur image di ho to compare karo.

    Mehenga kaam (page render / fetch) sirf ek baar hota hai — time aur images
    dono usi ek nateeje se nikalte hain.

    Time aur image alag-alag chalte hain: ek fail ho to doosra rukta nahi.
    Adhoora jawab poore na-jawab se behtar hai.
    """
    platform, m = reg.detect(url)
    if platform.id not in {p.id for p in reg.enabled()}:
        raise PlatformError(f"{platform.label} is deployment me enabled nahi hai",
                            platform=platform.id, reason="disabled")

    if uploaded is not None:
        decode(uploaded)            # kharab upload pe turant fail, download se pehle

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
                result.image_error = "post ki koi image download nahi ho payi"
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

    # Kuch bhi haath na aaya to asli exception hi wapas uthao, taaki uska reason
    # (invalid_id, not_visible, ...) bana rahe — generic 502 me badal dena galat hoga.
    if result.time is None and result.image is None:
        raise result._errors[0] if result._errors else PlatformError(
            "kuch nahi mila", platform=platform.id, reason="upstream_error")

    return result
