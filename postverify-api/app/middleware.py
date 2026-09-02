"""Cross-cutting request handling: correlation IDs, access logs, rate limiting.

Everything here exists to answer a question that only comes up once the service
is live: which request was that, how long did it take, and who is sending too
many of them.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import config

log = logging.getLogger("postverify.access")

REQUEST_ID_HEADER = "X-Request-ID"

# Endpoints worth rate limiting: each one can launch a browser and reach out to
# a social platform. The metadata endpoints are cheap and stay unlimited so that
# health checks are never throttled.
_EXPENSIVE_PREFIXES = ("/v1/",)


def client_key(request: Request) -> str:
    """Identify the caller for rate-limiting purposes.

    X-Forwarded-For is only trusted when the operator has said the service sits
    behind a proxy they control. Trusting it unconditionally would let any
    caller forge a new identity per request and bypass the limit entirely.
    """
    if config.trust_proxy_headers():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request ID, log the outcome, and return the ID to the caller.

    The ID is taken from the incoming header when present, so a trace started by
    a gateway or by the campaign portal continues through this service instead
    of restarting here.
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

        # 5xx is an operator's problem and 4xx is a caller's; separating the
        # levels keeps production alerting on the former only.
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
    """A fixed per-minute cap per client on the expensive endpoints.

    This is an in-process sliding window, which means each worker enforces its
    own share: four workers at 60/min allow 240/min in total. That is
    deliberate — it is a safety valve against one caller monopolising the
    browser pool, not a billing-grade quota. A deployment that needs an exact
    global limit should enforce it at the proxy, and set
    RATE_LIMIT_PER_MINUTE=0 here.
    """

    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, deque[float]] = {}

    async def dispatch(self, request: Request, call_next):
        limit = config.rate_limit_per_minute()
        path = request.url.path
        if limit <= 0 or not path.startswith(_EXPENSIVE_PREFIXES):
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
                "client": key, "path": path, "limit": limit,
            })
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "error": "rate_limited",
                    "message": (f"Too many requests. The limit is {limit} per "
                                f"minute; retry in {retry_after}s."),
                },
            )

        window.append(now)

        # Clients that have gone quiet must not accumulate. This is bounded
        # cleanup on a normal request, which is cheap enough to do inline and
        # avoids needing a background sweeper.
        if len(self._hits) > 1000:
            for stale in [k for k, v in self._hits.items()
                          if not v or now - v[-1] > 120]:
                self._hits.pop(stale, None)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative response headers.

    This service returns JSON, never HTML, so the strictest content policy
    applies with nothing to lose.
    """

    _HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    }

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for name, value in self._HEADERS.items():
            response.headers.setdefault(name, value)
        return response
