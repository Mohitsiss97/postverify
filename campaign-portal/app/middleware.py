"""Cross-cutting request handling: correlation IDs, access logs, rate limiting.

Everything here exists to answer questions that only arise once the portal is
live: which request was that, how long did it take, and who is sending too many
of them.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings

log = logging.getLogger("portal.access")

REQUEST_ID_HEADER = "X-Request-ID"

# Only the write paths are rate limited. Reads are cheap, and throttling status
# polling would work against the participant: the UI polls while a submission is
# being verified, which is exactly when they most need an answer.
_LIMITED_PREFIXES = ("/v1/submissions", "/v1/campaigns")
_LIMITED_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def client_key(request: Request) -> str:
    """Identify the caller for rate-limiting purposes.

    The participant's own identity is preferred when present, because several
    people behind one office NAT share an IP address and should not consume each
    other's allowance. X-Forwarded-For is trusted only when the operator has
    said the service sits behind a proxy they control; trusting it otherwise
    would let a caller forge a fresh identity per request.
    """
    user = request.headers.get(settings.user_header.lower())
    if user and user.strip():
        return f"user:{user.strip()}"
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request ID, log the outcome, and return the ID to the caller.

    The ID is taken from the incoming header when one is present, so a trace
    started by a gateway continues through the portal rather than restarting
    here. The portal in turn forwards it to the verification engine, which makes
    a single submission followable across both services.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.exception("request failed", extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "duration_ms": duration_ms,
            })
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[REQUEST_ID_HEADER] = request_id

        # 5xx is an operator's problem and 4xx is a caller's. Separating the
        # levels keeps production alerting focused on the former.
        level = logging.ERROR if response.status_code >= 500 else logging.INFO
        log.log(level, "%s %s -> %s", request.method, request.url.path,
                response.status_code, extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "client": client_key(request),
                })
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A per-minute cap per client on the write endpoints.

    This is an in-process sliding window, so each worker enforces its own share:
    four workers at 120/min allow 480/min in total. That is deliberate — it is a
    safety valve against one caller flooding the queue, not a billing-grade
    quota. A deployment needing an exact global limit should enforce it at the
    proxy and set RATE_LIMIT_PER_MINUTE=0 here.
    """

    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, deque[float]] = {}

    def _applies(self, request: Request) -> bool:
        return (request.method in _LIMITED_METHODS
                and request.url.path.startswith(_LIMITED_PREFIXES))

    async def dispatch(self, request: Request, call_next):
        limit = settings.rate_limit_per_minute
        if limit <= 0 or not self._applies(request):
            return await call_next(request)

        now = time.monotonic()
        key = client_key(request)
        window = self._hits.setdefault(key, deque())
        while window and now - window[0] > 60:
            window.popleft()

        if len(window) >= limit:
            retry_after = max(1, int(60 - (now - window[0])))
            log.warning("rate limit hit", extra={
                "request_id": getattr(request.state, "request_id", None),
                "client": key, "path": request.url.path, "limit": limit,
            })
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "error": "rate_limited",
                    "message": (f"Too many requests. The limit is {limit} per "
                                f"minute; try again in {retry_after}s."),
                },
            )

        window.append(now)

        # Clients that have gone quiet must not accumulate. Bounded cleanup on a
        # normal request is cheap enough to do inline and avoids a sweeper task.
        if len(self._hits) > 2000:
            for stale in [k for k, v in self._hits.items()
                          if not v or now - v[-1] > 120]:
                self._hits.pop(stale, None)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative response headers for both the API and the bundled UI.

    The content policy permits inline styles and scripts because the UI is a
    single self-contained HTML file. That is a real weakening of the policy, and
    it is only acceptable because the page renders no user-supplied HTML: every
    value from the API reaches the DOM through textContent, never innerHTML.
    Should that ever change, the inline blocks must be moved into files and
    'unsafe-inline' dropped.
    """

    _HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "form-action 'self'"
        ),
    }

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for name, value in self._HEADERS.items():
            response.headers.setdefault(name, value)
        return response
