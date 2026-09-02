"""postverify-api ko HTTP se call karna.

Portal us service ka code copy nahi karta — usse baat karta hai. Isse dono alag
deploy aur scale ho sakte hain (engine browser-heavy hai, portal halka), aur
engine ka folder chhune ki zaroorat nahi padti.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from .config import settings
from .enums import RejectReason


class EngineError(Exception):
    """Engine se kaam nahi ho paya. `reason` batata hai user ko kya kehna hai."""

    def __init__(self, reason: RejectReason, message: str, *,
                 status: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status = status
        self.payload = payload or {}


@dataclass
class EngineResult:
    """Engine ke jawab ka wo hissa jo portal ko chahiye."""
    platform: str | None = None
    post_id: str | None = None
    canonical_url: str | None = None
    published_at: datetime | None = None
    age_seconds: int | None = None
    time_method: str | None = None

    image_present: bool | None = None
    image_verdict: str | None = None
    image_score: int | None = None
    images_checked: int | None = None
    matched_tier: str | None = None

    status_code: int = 200
    duration_ms: int = 0
    raw: dict = field(default_factory=dict)


# Engine ka error code -> portal ka reason. Jo yahan na ho wo takneeki maana
# jayega (retry hoga) — kyunki anjaan error pe user ko reject kehna galat hoga.
_ERROR_MAP: dict[str, RejectReason] = {
    "unsupported_url": RejectReason.UNSUPPORTED_URL,
    "not_visible": RejectReason.POST_NOT_FOUND,
    "invalid_id": RejectReason.POST_NOT_FOUND,
    "no_media": RejectReason.NO_IMAGE_IN_POST,
    "bad_image": RejectReason.NO_CAMPAIGN_ASSETS,
    "unauthorized": RejectReason.ENGINE_UNAVAILABLE,
    "not_configured": RejectReason.ENGINE_UNAVAILABLE,
    "disabled": RejectReason.ENGINE_UNAVAILABLE,
    "upstream_error": RejectReason.ENGINE_UNAVAILABLE,
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc)
    except ValueError:
        return None


def _shape(payload: dict, status: int, duration_ms: int) -> EngineResult:
    t = payload.get("time") or {}
    img = payload.get("image") or {}
    matched = img.get("matched") or {}
    return EngineResult(
        platform=payload.get("platform"),
        post_id=payload.get("post_id"),
        canonical_url=payload.get("canonical_url"),
        published_at=_parse_iso(t.get("published_at")),
        age_seconds=t.get("age_seconds"),
        time_method=t.get("method"),
        image_present=img.get("present"),
        image_verdict=img.get("verdict"),
        image_score=img.get("score"),
        images_checked=img.get("images_checked"),
        matched_tier=matched.get("tier"),
        status_code=status,
        duration_ms=duration_ms,
        raw=payload,
    )


def _raise_for(payload: dict, status: int) -> None:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        code = str(detail.get("error", ""))
        message = str(detail.get("message", "")) or "Verification fail hua"
    else:
        code, message = "", str(detail or "Verification fail hua")

    reason = _ERROR_MAP.get(code)
    if reason is None:
        # Anjaan error = takneeki dikkat maano. User ko galat se reject karne se
        # behtar hai dobara koshish karna.
        reason = RejectReason.ENGINE_UNAVAILABLE
    raise EngineError(reason, message, status=status, payload=payload)


class VerificationEngine:
    """postverify-api ka client. Ek hi AsyncClient reuse hota hai."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float | None = None):
        self.base_url = (base_url or settings.engine_url).rstrip("/")
        self.token = token if token is not None else settings.engine_token
        self.timeout = timeout or settings.engine_timeout_seconds
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def health(self) -> dict:
        try:
            client = await self._http()
            r = await client.get("/health", timeout=10.0)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise EngineError(RejectReason.ENGINE_UNAVAILABLE,
                              f"Engine tak nahi pahunche: {e}") from e

    async def verify(self, post_url: str, image: bytes, *,
                     filename: str = "asset.jpg") -> EngineResult:
        """Ek call me dono: post ka time, aur di hui image match hui ya nahi."""
        started = time.monotonic()
        data: dict[str, str] = {"url": post_url}
        if self.token:
            data["token"] = self.token

        try:
            client = await self._http()
            response = await client.post(
                "/v1/verify",
                data=data,
                files={"image": (filename, image, "application/octet-stream")},
            )
        except httpx.TimeoutException as e:
            raise EngineError(
                RejectReason.ENGINE_UNAVAILABLE,
                "Verification service ne waqt pe jawab nahi diya") from e
        except httpx.HTTPError as e:
            raise EngineError(RejectReason.ENGINE_UNAVAILABLE,
                              f"Verification service tak nahi pahunche: {e}") from e

        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            payload = response.json()
        except ValueError as e:
            raise EngineError(
                RejectReason.ENGINE_UNAVAILABLE,
                f"Engine ne JSON nahi bheja (HTTP {response.status_code})") from e

        if response.status_code >= 400:
            _raise_for(payload, response.status_code)
        return _shape(payload, response.status_code, duration_ms)


engine_client = VerificationEngine()
