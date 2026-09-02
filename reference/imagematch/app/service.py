"""Orchestration: uploaded image + post URL -> kya ye image us post me hai?

Kaam ka silsila:
    1. URL se platform pehchano (ya user ne jo chuna hai usi pe chalao)
    2. Post se saari images nikalo
    3. Har ek ko download karke uploaded image se compare karo
    4. Sabse acha match batao
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field

from . import fetch
from . import media as reg
from .compare import Comparison, ImageError, compare, decode
from .media import (
    ExtractionError,
    ImageRef,
    Source,
    UnsupportedURLError,
    WrongPlatformError,
)

# Ek post pe itni se zyada images check nahi karte — bachav, taaki koi page
# 200 images dekar service ko na bitha de.
MAX_IMAGES = 12

# Kitni images ek saath download hon
_DOWNLOAD_CONCURRENCY = 6

_RANK = {"identical": 3, "same": 2, "likely": 1, "different": 0}


@dataclass
class Candidate:
    """Post ki ek image, aur uska comparison result."""
    url: str
    tier: str
    label: str
    verdict: str = "error"
    confidence: float = 0.0
    phash_distance: int | None = None
    orb_inliers: int = 0
    note: str = ""
    error: str | None = None

    @property
    def rank(self) -> int:
        return _RANK.get(self.verdict, -1)


@dataclass
class MatchResult:
    present: bool
    verdict: str
    platform: str
    platform_label: str
    post_id: str
    canonical_url: str
    summary: str
    images_checked: int
    matched: Candidate | None = None
    candidates: list[Candidate] = field(default_factory=list)

    def dict(self) -> dict:
        return asdict(self)


def _collapse_groups(images: list[ImageRef]) -> list[list[ImageRef]]:
    """Ek group = ek hi image ke alag resolutions. Har group ki ek list."""
    groups: list[list[ImageRef]] = []
    by_key: dict[str, list[ImageRef]] = {}
    for ref in images:
        if not ref.group:
            groups.append([ref])
            continue
        by_key.setdefault(ref.group, []).append(ref)
    groups.extend(by_key.values())
    return groups


async def _download_group(group: list[ImageRef]) -> tuple[ImageRef, bytes | None, str | None]:
    """Group me se pehli image jo download ho jaye."""
    last_error = None
    for ref in group:
        try:
            return ref, await fetch.get_image(ref.url), None
        except fetch.FetchError as e:
            last_error = str(e)
    return group[0], None, last_error


async def _compare_one(uploaded: bytes, group: list[ImageRef],
                       gate: asyncio.Semaphore) -> Candidate:
    async with gate:
        ref, data, error = await _download_group(group)

    cand = Candidate(url=ref.url, tier=ref.tier, label=ref.label)
    if data is None:
        cand.error = error or "download nahi hui"
        return cand

    try:
        # OpenCV blocking hai — event loop ko rokne se bachao
        result: Comparison = await asyncio.to_thread(compare, uploaded, data)
    except ImageError as e:
        cand.error = str(e)
        return cand

    cand.verdict = result.verdict
    cand.confidence = round(result.confidence, 3)
    cand.phash_distance = result.phash_distance
    cand.orb_inliers = result.orb_inliers
    cand.note = result.note
    return cand


def _summary(best: Candidate | None, checked: int, source: Source) -> str:
    if best is None or best.rank <= 0:
        return f"Ye image is post me nahi mili ({checked} image check ki gayi)"
    where = "post ki apni image" if best.tier == "post" else "post page pe mili image"
    if best.verdict == "identical":
        return f"Haan — bilkul wahi file hai ({where})"
    if best.verdict == "same":
        return f"Haan — wahi image hai ({where}). {best.note}"
    return (f"Shayad — {where} se kaafi milti hai, par pakka nahi. "
            f"Khud dekh lijiye")


async def match_with(source: Source, url: str, uploaded: bytes) -> MatchResult:
    """User ne platform chuna hai — usi ki service chalao."""
    m = reg.match_on(source, url)
    if m is None:
        try:
            other, _ = reg.detect(url)
        except UnsupportedURLError:
            raise UnsupportedURLError(
                f"Ye {source.label} ka valid post URL nahi lagta. "
                f"Example: {source.sample_url}") from None
        raise WrongPlatformError(
            expected=source.id, actual=other.id, url=url,
            expected_label=source.label, actual_label=other.label)

    decode(uploaded)          # jaldi fail ho jao agar upload hi kharab hai

    images = await source.images(m)
    groups = _collapse_groups(images)[:MAX_IMAGES]

    gate = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)
    candidates = await asyncio.gather(
        *(_compare_one(uploaded, g, gate) for g in groups))

    usable = [c for c in candidates if c.error is None]
    if not usable:
        raise ExtractionError(
            "Post ki koi image download ya read nahi ho payi",
            platform=source.id, reason="upstream_error")

    # Best: pehle verdict, phir post-tier ko preference, phir confidence
    best = max(usable, key=lambda c: (c.rank, c.tier == "post", c.confidence))
    matched = best if best.rank > 0 else None

    return MatchResult(
        present=best.rank > 0,
        verdict=best.verdict,
        platform=source.id,
        platform_label=source.label,
        post_id=m.post_id,
        canonical_url=m.canonical_url,
        summary=_summary(best, len(usable), source),
        images_checked=len(usable),
        matched=matched,
        candidates=sorted(candidates, key=lambda c: -c.rank),
    )


async def match(url: str, uploaded: bytes) -> MatchResult:
    """Platform khud detect karke chalao."""
    source, _ = reg.detect(url)
    if source.id not in {s.id for s in reg.enabled()}:
        raise ExtractionError(
            f"{source.label} is deployment me enabled nahi hai",
            platform=source.id, reason="disabled")
    return await match_with(source, url, uploaded)
