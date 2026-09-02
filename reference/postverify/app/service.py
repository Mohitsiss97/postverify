"""Orchestration: URL -> post ka time aur uski images; phir image se compare.

Do kadam me bant diya hai, kyunki mehenga kaam sirf pehle kadam me hai:

    prepare(url)      platform detect -> ek render/fetch -> time -> images
                      download karke apne temp store me. Yahi 6-15 second leta hai.
    check(session)    uploaded image ko already-downloaded images se compare.
                      Ye 100ms ka kaam hai — koi network nahi.

Isse preview aur check dono ek hi render pe chalte hain. User pehle post dekh
leta hai, phir image daalta hai, aur jawab turant milta hai.

Data ka ant: check hote hi session delete. Chhoot jaye to TTL sweep. Service band
ho to poora root. Upload ki hui image kabhi disk pe nahi jaati.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import fetch
from . import platforms as reg
from .compare import Comparison, ImageError, compare, decode
from .platforms import ImageRef, PlatformError
from .store import Session, store, suffix_for

MAX_IMAGES = 12
_DOWNLOAD_CONCURRENCY = 6
_RANK = {"identical": 3, "same": 2, "likely": 1, "different": 0}


@dataclass
class StoredImage:
    """Post ki ek image jo humne download karke rakh li hai."""
    name: str               # session folder me file ka naam
    url: str                # /media/<token>/<name> — apne origin se
    tier: str               # post | page
    label: str
    source_url: str         # asli CDN link, reference ke liye
    bytes: int


@dataclass
class PostTime:
    published_at: datetime
    published_at_local: str | None
    timezone: str | None
    age_seconds: int
    age_human: str
    method: str
    precision: str


@dataclass
class Prepared:
    session: str
    platform: str
    platform_label: str
    post_id: str
    canonical_url: str
    time: PostTime | None = None
    time_error: str | None = None
    images: list[StoredImage] = field(default_factory=list)
    image_error: str | None = None
    summary: str = ""

    def dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    url: str                # local /media/... url
    tier: str
    label: str
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
class Verification:
    platform: str
    platform_label: str
    post_id: str
    canonical_url: str
    time: PostTime | None = None
    time_error: str | None = None
    image_checked: bool = False
    present: bool | None = None
    score: int | None = None
    verdict: str | None = None
    matched: Candidate | None = None
    images_checked: int = 0
    image_error: str | None = None
    summary: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    cleaned_up: bool = False

    def dict(self) -> dict:
        return asdict(self)


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
            local, tzname = None, None
    age = int((datetime.now(timezone.utc) - dt).total_seconds())
    return PostTime(dt, local, tzname, age, _age_human(age),
                    timing.method, timing.precision)


# --- images -------------------------------------------------------------

def _collapse_groups(images: list[ImageRef]) -> list[list[ImageRef]]:
    """Ek group = ek hi image ke alag resolutions."""
    groups: list[list[ImageRef]] = []
    by_key: dict[str, list[ImageRef]] = {}
    for ref in images:
        if not ref.group:
            groups.append([ref])
        else:
            by_key.setdefault(ref.group, []).append(ref)
    groups.extend(by_key.values())
    return groups


async def _download_group(group: list[ImageRef]):
    """Group me se pehli image jo download ho jaye."""
    last_error = None
    for ref in group:
        try:
            return ref, await fetch.get_image(ref.url), None
        except fetch.FetchError as e:
            last_error = str(e)
    return group[0], None, last_error


async def _fetch_all(groups: list[list[ImageRef]], session: Session) -> list[StoredImage]:
    gate = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)

    async def one(index: int, group: list[ImageRef]):
        async with gate:
            ref, data, _error = await _download_group(group)
        if data is None:
            return None
        name = store.put(session, index, data, suffix_for(None, ref.url))
        return StoredImage(name=name, url=f"/media/{session.token}/{name}",
                           tier=ref.tier, label=ref.label,
                           source_url=ref.url, bytes=len(data))

    done = await asyncio.gather(*(one(i, g) for i, g in enumerate(groups)))
    return [d for d in done if d is not None]


# --- summaries ----------------------------------------------------------

def _time_line(t: PostTime | None) -> str:
    if not t:
        return ""
    when = (t.published_at_local or t.published_at.isoformat())[:16].replace("T", " ")
    return f"Post {when} ko upload hua ({t.age_human})"


def _prepared_summary(p: Prepared) -> str:
    bits = [b for b in (_time_line(p.time),) if b]
    if p.image_error:
        bits.append(f"Images nahi mili: {p.image_error}")
    elif p.images:
        bits.append(f"{len(p.images)} image mili — ab apni image daaliye")
    return ". ".join(bits) or "Kuch nahi mila"


def _verify_summary(v: Verification) -> str:
    bits = [b for b in (_time_line(v.time),) if b]
    if not bits and v.time_error:
        bits.append("Time nahi mila")

    if not v.image_checked:
        return bits[0] if bits else "Kuch nahi mila"

    if v.image_error:
        bits.append(f"Image check nahi ho payi: {v.image_error}")
    elif v.present:
        where = ("post ki apni image" if v.matched and v.matched.tier == "post"
                 else "post page pe mili image")
        word = "bilkul wahi image" if v.verdict == "identical" else "wahi image"
        bits.append(f"Aapki image is post me hai — {word} ({where}), {v.score}% match")
    else:
        best = max((c.score for c in v.candidates if c.error is None), default=0)
        bits.append(f"Aapki image is post me nahi mili "
                    f"({v.images_checked} image check ki, sabse zyada {best}% mila)")
    return ". ".join(bits)


# --- step 1: prepare ----------------------------------------------------

async def prepare(url: str, *, tz: str | None = None,
                  with_images: bool = True) -> Prepared:
    """URL se time nikalo, aur (chaho to) post ki images download karke rakh lo.

    with_images=False tab kaam aata hai jab sirf time chahiye — X aur LinkedIn pe
    tab ek bhi network call nahi hoti, kyunki unka time ID me hi hota hai.
    """
    platform, m = reg.detect(url)
    if platform.id not in {p.id for p in reg.enabled()}:
        raise PlatformError(f"{platform.label} is deployment me enabled nahi hai",
                            platform=platform.id, reason="disabled")

    session = store.create()
    result = Prepared(session=session.token, platform=platform.id,
                      platform_label=platform.label, post_id=m.post_id,
                      canonical_url=m.canonical_url)
    time_exc: PlatformError | None = None
    image_exc: PlatformError | None = None
    try:
        ctx = await platform.load(m)

        try:
            result.time = _build_time(await platform.published_at(m, ctx), tz)
        except PlatformError as e:
            result.time_error = str(e)
            time_exc = e

        if with_images:
            try:
                groups = _collapse_groups(await platform.images(m, ctx))[:MAX_IMAGES]
                result.images = await _fetch_all(groups, session)
                if not result.images:
                    result.image_error = "post ki koi image download nahi ho payi"
            except PlatformError as e:
                result.image_error = str(e)
                image_exc = e

        # Kuch bhi haath na aaya to session rakhne ka matlab nahi. Aur asli
        # exception hi wapas uthate hain, taaki uska reason (invalid_id,
        # not_visible, ...) bana rahe — generic 502 me badal dena galat hoga.
        if result.time is None and not result.images:
            store.drop(session.token)
            raise time_exc or image_exc or PlatformError(
                "kuch nahi mila", platform=platform.id, reason="upstream_error")
    except Exception:
        store.drop(session.token)
        raise

    result.summary = _prepared_summary(result)
    session.payload = result        # /verify?session= isi ko dobara use karta hai
    return result


# --- step 2: check ------------------------------------------------------

def _compare_stored(uploaded: bytes, image: StoredImage, data: bytes) -> Candidate:
    cand = Candidate(url=image.url, tier=image.tier, label=image.label)
    try:
        result: Comparison = compare(uploaded, data)
    except ImageError as e:
        cand.error = str(e)
        return cand
    cand.verdict = result.verdict
    cand.score = result.score
    cand.phash_distance = result.phash_distance
    cand.orb_inliers = result.orb_inliers
    cand.note = result.note
    return cand


async def check(prepared: Prepared, uploaded: bytes, *,
                cleanup: bool = True) -> Verification:
    """Uploaded image ko already-downloaded images se milao. Koi network nahi."""
    decode(uploaded)          # kharab upload pe turant fail karo

    result = Verification(platform=prepared.platform,
                          platform_label=prepared.platform_label,
                          post_id=prepared.post_id,
                          canonical_url=prepared.canonical_url,
                          time=prepared.time, time_error=prepared.time_error,
                          image_checked=True)

    session = store.get(prepared.session)
    if session is None:
        result.image_error = "preview ka data expire ho gaya — URL dobara daaliye"
        result.image_checked = False
        result.summary = _verify_summary(result)
        return result

    if not prepared.images:
        result.image_error = prepared.image_error or "post pe koi image nahi"
        result.summary = _verify_summary(result)
        if cleanup:
            result.cleaned_up = store.drop(prepared.session)
        return result

    def work() -> list[Candidate]:
        out = []
        for image in prepared.images:
            data = store.read(session, image.name)
            if data is None:
                cand = Candidate(url=image.url, tier=image.tier, label=image.label)
                cand.error = "file mil nahi rahi"
                out.append(cand)
                continue
            out.append(_compare_stored(uploaded, image, data))
        return out

    # OpenCV blocking hai — event loop ko rokne se bachao
    cands = await asyncio.to_thread(work)
    usable = [c for c in cands if c.error is None]

    if not usable:
        result.image_error = "post ki images read nahi ho payin"
    else:
        best = max(usable, key=lambda c: (c.rank, c.tier == "post", c.score))
        result.images_checked = len(usable)
        result.present = best.rank > 0
        result.verdict = best.verdict
        result.score = best.score
        result.matched = best if best.rank > 0 else None
        result.candidates = sorted(cands, key=lambda c: (-c.rank, -c.score))

    result.summary = _verify_summary(result)
    if cleanup:
        result.cleaned_up = store.drop(prepared.session)
    return result


# --- ek hi call me sab (API users ke liye) ------------------------------

async def verify(url: str, uploaded: bytes | None = None, *,
                 tz: str | None = None) -> Verification:
    """prepare + check + cleanup, ek saath.

    Image na di ho to sirf time — aur session turant delete, kyunki uska aage
    koi kaam hi nahi.
    """
    if uploaded is not None:
        decode(uploaded)      # download shuru karne se pehle upload check kar lo

    prepared = await prepare(url, tz=tz, with_images=uploaded is not None)

    if uploaded is None:
        store.drop(prepared.session)
        result = Verification(platform=prepared.platform,
                              platform_label=prepared.platform_label,
                              post_id=prepared.post_id,
                              canonical_url=prepared.canonical_url,
                              time=prepared.time, time_error=prepared.time_error,
                              cleaned_up=True)
        result.summary = _verify_summary(result)
        return result

    return await check(prepared, uploaded)
