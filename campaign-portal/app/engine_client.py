"""The HTTP client for postverify-api.

The portal does not copy the engine's code; it talks to it. That lets the two be
deployed and scaled independently — the engine is browser-heavy and the portal
is light — and means the engine's source tree is never touched from here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from .config import settings
from .enums import RejectReason


class EngineError(Exception):
    """The engine could not complete the work.

    `reason` determines what the participant is told, and whether the submission
    is retried or rejected outright.
    """

    def __init__(self, reason: RejectReason, message: str, *,
                 status: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status = status
        self.payload = payload or {}


@dataclass
class EngineResult:
    """The part of the engine's response the portal actually uses."""
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


# Engine error code -> portal reason. Anything absent from this map is treated
# as a technical failure and retried, because telling a participant their post
# was rejected on the strength of an error we do not recognise would be wrong.
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
    "rate_limited": RejectReason.ENGINE_UNAVAILABLE,
}

_GENERIC_FAILURE = "Verification failed"


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


def _error_code_and_message(payload: dict) -> tuple[str, str]:
    """Read the error out of a response, accepting both envelope shapes.

    The engine now returns errors flat, as {"error", "message"}. Older builds
    wrapped them in FastAPI's "detail". Both are accepted so that the portal
    keeps working across a rolling deployment where the two services are briefly
    on different versions.
    """
    if isinstance(payload.get("error"), str):
        return payload["error"], str(payload.get("message") or _GENERIC_FAILURE)

    detail = payload.get("detail")
    if isinstance(detail, dict):
        return (str(detail.get("error", "")),
                str(detail.get("message", "")) or _GENERIC_FAILURE)
    return "", str(detail or _GENERIC_FAILURE)


def _raise_for(payload: dict, status: int) -> None:
    code, message = _error_code_and_message(payload)
    # An unrecognised code means a technical problem. Retrying is better than
    # rejecting a participant on the basis of an error we cannot interpret.
    reason = _ERROR_MAP.get(code, RejectReason.ENGINE_UNAVAILABLE)
    raise EngineError(reason, message, status=status, payload=payload)


class VerificationEngine:
    """The postverify-api client. One AsyncClient is reused across calls."""

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
                              f"The engine could not be reached: {e}") from e

    async def verify(self, post_url: str, image: bytes, *,
                     filename: str = "asset.jpg",
                     request_id: str | None = None) -> EngineResult:
        """One call for both questions: when the post was published, and whether
        the given image appears in it.

        The request ID is forwarded so that a submission can be followed across
        both services in the logs.
        """
        started = time.monotonic()
        data: dict[str, str] = {"url": post_url}
        if self.token:
            data["token"] = self.token
        headers = {"X-Request-ID": request_id} if request_id else None

        try:
            client = await self._http()
            response = await client.post(
                "/v1/verify",
                data=data,
                files={"image": (filename, image, "application/octet-stream")},
                headers=headers,
            )
        except httpx.TimeoutException as e:
            raise EngineError(
                RejectReason.ENGINE_UNAVAILABLE,
                "The verification service did not respond in time") from e
        except httpx.HTTPError as e:
            raise EngineError(
                RejectReason.ENGINE_UNAVAILABLE,
                f"The verification service could not be reached: {e}") from e

        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            payload = response.json()
        except ValueError as e:
            raise EngineError(
                RejectReason.ENGINE_UNAVAILABLE,
                f"The engine did not return JSON (HTTP {response.status_code})") from e

        if response.status_code >= 400:
            _raise_for(payload, response.status_code)
        return _shape(payload, response.status_code, duration_ms)


engine_client = VerificationEngine()
