"""PostVerify API — three endpoints, and nothing else.

    POST /v1/time      a URL              -> when the post was published
    POST /v1/within    a URL + windows    -> whether it falls inside 1d/3d/7d/15d/1m
    POST /v1/verify    a URL + an image   -> whether that image is in the post

Each endpoint also has a GET form, except verify, which carries a file upload.

There is no web page here, no session and no temporary file. Images from the
post exist only in the memory of the request that fetched them.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, fetch, window
from . import platforms as reg
from .http import fail, guard, read_upload
from .logging_setup import configure_logging
from .middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from .service import Result, resolve

log = logging.getLogger("postverify")

TAGS = [
    {
        "name": "Time",
        "description": "When the post was published. If that is all you need, no "
                       "images are downloaded at all — and on X and LinkedIn no "
                       "network call is made either.",
    },
    {
        "name": "Window",
        "description": "How recent the post is. Pass as many windows as you need, "
                       "comma-separated — `1d,3d,7d,15d,1m` — and each one is "
                       "answered with a plain true or false. Note that `m` means "
                       "month here, not minute; use `min` for minutes.",
    },
    {
        "name": "Image",
        "description": "Whether a given image appears in the post. Recognised even "
                       "after resizing, cropping, recompression or watermarking.",
    },
    {"name": "Meta", "description": "Liveness, and what this deployment supports."},
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    # Fail fast: a misconfigured instance should never accept its first request.
    warnings = config.validate()
    log.info("starting %s", config.APP_NAME, extra={"config": config.describe()})
    for warning in warnings:
        log.warning(warning)
    yield
    log.info("shutting down")


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    description=(
        "Give it the URL of a social media post and it returns the upload time, a "
        "freshness check, and an image match. The platform is detected from the "
        "URL: X, Instagram, Facebook, LinkedIn and YouTube. No login and no API "
        "key are required."
    ),
    openapi_tags=TAGS,
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)

if config.cors_origins():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins(),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Access-Token", "X-Request-ID"],
    )


# --- one error shape ----------------------------------------------------
#
# Every failure leaves this service as {"error": "...", "message": "..."}, so a
# client writes one parser rather than one per framework layer. Without these
# handlers FastAPI wraps some errors in "detail" and others not, and callers end
# up guessing.

_FALLBACK = {
    400: ("bad_request", "The request could not be understood"),
    401: ("unauthorized", "Authentication is required"),
    403: ("forbidden", "This is not allowed"),
    404: ("not_found", "No such resource"),
    405: ("method_not_allowed", "That method is not supported on this path"),
    413: ("too_large", "The payload is too large"),
    429: ("rate_limited", "Too many requests"),
    500: ("internal_error", "Something went wrong on our side"),
    502: ("upstream_error", "An upstream service did not respond correctly"),
    503: ("unavailable", "The service is temporarily unavailable"),
}


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        content = dict(detail)
    else:
        code, message = _FALLBACK.get(exc.status_code,
                                      ("http_error", "The request could not be completed"))
        content = {"error": code, "message": message}
    return JSONResponse(status_code=exc.status_code, content=content,
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Only field, message and type are emitted. Pydantic's raw error objects are
    # not JSON-serialisable and can carry internals that should not be exposed.
    fields = [{
        "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
        "message": err.get("msg", ""),
        "type": err.get("type", ""),
    } for err in exc.errors()]
    return JSONResponse(status_code=422, content={
        "error": "invalid_request",
        "message": "The request did not pass validation",
        "fields": fields,
    })


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log the detail, but never return it.

    An unhandled exception's text routinely contains file paths, SQL and
    occasionally credentials. The caller gets the request ID instead, which is
    enough to find the full trace in the logs.
    """
    request_id = getattr(request.state, "request_id", None)
    log.exception("unhandled error", extra={"request_id": request_id,
                                            "path": request.url.path})
    return JSONResponse(status_code=500, content={
        "error": "internal_error",
        "message": "Something went wrong on our side",
        "request_id": request_id,
    })


# --- request models -----------------------------------------------------

_URL = Field(..., examples=["https://www.instagram.com/p/DceLPdrCR3L/"],
             description="A post URL on any supported platform")
_TZ = Field(None, examples=["Asia/Kolkata"],
            description="IANA timezone, if you also want the local time")
_TOKEN = Field(None, description="Required when ACCESS_TOKEN is set "
                                 "(the X-Access-Token header works too)")
_WITHIN = Field(..., examples=["1d,3d,7d,15d,1m"],
                description="Comma-separated windows. "
                            "Units: s, min, h, d, w, m/mo (month), y")


class TimeIn(BaseModel):
    url: str = _URL
    tz: str | None = _TZ
    token: str | None = _TOKEN


class WithinIn(BaseModel):
    url: str = _URL
    within: str = _WITHIN
    tz: str | None = _TZ
    token: str | None = _TOKEN


# --- response shaping ---------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _base(result: Result) -> dict:
    body: dict = {
        "ok": True,
        "platform": result.platform,
        "platform_label": result.platform_label,
        "post_id": result.post_id,
        "canonical_url": result.canonical_url,
        "time": None,
    }
    if result.time:
        t = result.time
        body["time"] = {
            "published_at": _iso(t.published_at),
            "published_at_local": t.published_at_local,
            "timezone": t.timezone,
            "age_seconds": t.age_seconds,
            "age_human": t.age_human,
            "method": t.method,
            "precision": t.precision,
        }
    else:
        body["time_error"] = result.time_error
    return body


def _windows_or_400(raw: str | None) -> list[window.Window]:
    try:
        return window.parse(raw)
    except window.WindowError as e:
        raise HTTPException(400, {"error": "bad_window", "message": str(e)}) from e


def _add_within(body: dict, result: Result, windows: list[window.Window]) -> dict:
    if not windows:
        return body
    if result.time is None:
        # Answering the window question without a timestamp would be a lie, and
        # a false here reads as "the post is old" rather than "we do not know".
        body["within"] = None
        body["within_error"] = ("the upload time could not be determined, so the "
                                "window cannot be checked")
        return body

    checked = window.evaluate(windows, result.time.published_at)
    body["within"] = checked["results"]
    body["within_detail"] = checked["windows"]
    body["checked_at"] = checked["checked_at"]
    if len(windows) == 1:
        # With a single window, also give a plain boolean so the caller does not
        # have to dig into a nested object for it.
        body["is_within"] = checked["results"][windows[0].label]
    return body


def _add_image(body: dict, result: Result) -> dict:
    if result.image_error:
        body["image"] = {"checked": False, "error": result.image_error}
        return body
    if result.image is None:
        return body
    im = result.image
    image: dict = {
        "checked": True,
        "present": im.present,
        "verdict": im.verdict,
        "score": im.score,
        "images_checked": im.images_checked,
    }
    if im.matched:
        image["matched"] = {
            # tier "post" means the image definitively belongs to this post;
            # "page" means it was found on the post's page and could be a
            # carousel slide or a related post.
            "tier": im.matched.tier,
            "score": im.matched.score,
            "orb_inliers": im.matched.orb_inliers,
            "phash_distance": im.matched.phash_distance,
        }
    body["image"] = image
    return body


async def _run(url: str, *, tz: str | None = None,
               uploaded: bytes | None = None) -> Result:
    try:
        return await resolve(url, tz=tz, uploaded=uploaded)
    except HTTPException:
        raise
    except Exception as e:
        raise fail(e) from e


# --- 1. time ------------------------------------------------------------

@app.post("/v1/time", tags=["Time"], summary="When the post was published")
async def time_post(body: TimeIn,
                    x_access_token: str | None = Header(None)) -> dict:
    guard(body.token, x_access_token)
    return _base(await _run(body.url, tz=body.tz))


@app.get("/v1/time", tags=["Time"], summary="The same, via query parameters")
async def time_get(url: str = Query(..., description="The post URL"),
                   tz: str | None = Query(None),
                   token: str | None = Query(None),
                   x_access_token: str | None = Header(None)) -> dict:
    guard(token, x_access_token)
    return _base(await _run(url, tz=tz))


# --- 2. within ----------------------------------------------------------

@app.post("/v1/within", tags=["Window"],
          summary="Whether the post falls within 1d / 3d / 7d / 15d / 1m")
async def within_post(body: WithinIn,
                      x_access_token: str | None = Header(None)) -> dict:
    guard(body.token, x_access_token)
    # Parse the windows before doing any work, so a malformed window costs
    # nothing rather than being discovered after a browser render.
    windows = _windows_or_400(body.within)
    result = await _run(body.url, tz=body.tz)
    return _add_within(_base(result), result, windows)


@app.get("/v1/within", tags=["Window"], summary="The same, via query parameters")
async def within_get(url: str = Query(..., description="The post URL"),
                     within: str = Query(..., examples=["1d,3d,7d,15d,1m"]),
                     tz: str | None = Query(None),
                     token: str | None = Query(None),
                     x_access_token: str | None = Header(None)) -> dict:
    guard(token, x_access_token)
    windows = _windows_or_400(within)
    result = await _run(url, tz=tz)
    return _add_within(_base(result), result, windows)


# --- 3. verify ----------------------------------------------------------

@app.post("/v1/verify", tags=["Image"],
          summary="Whether this image appears in that post")
async def verify_post(
    url: str = Form(..., description="The post URL"),
    image: UploadFile | None = File(None, description="The image to look for"),
    image_url: str | None = Form(
        None, description="An image URL instead of a file, for server-to-server use"),
    within: str | None = Form(
        None, examples=["7d"],
        description="Optional. Supplying it here avoids a separate /v1/within "
                    "call, which on Instagram and Facebook saves a second "
                    "browser render."),
    tz: str | None = Form(None),
    token: str | None = Form(None),
    x_access_token: str | None = Header(None),
) -> dict:
    guard(token, x_access_token)
    windows = _windows_or_400(within)

    uploaded = await read_upload(image)
    if uploaded is None and image_url:
        try:
            uploaded = await fetch.get_image(image_url)
        except fetch.FetchError as e:
            raise HTTPException(400, {
                "error": "bad_image",
                "message": f"The image could not be fetched from image_url: {e}"}) from e
    if uploaded is None:
        raise HTTPException(400, {
            "error": "bad_image",
            "message": "Either image or image_url must be supplied"})

    result = await _run(url, tz=tz, uploaded=uploaded)
    return _add_image(_add_within(_base(result), result, windows), result)


# --- meta ---------------------------------------------------------------

@app.get("/health", tags=["Meta"], summary="Liveness")
async def health() -> dict:
    return {
        "status": "ok",
        "version": config.APP_VERSION,
        "env": config.env(),
        "platforms": [p.id for p in reg.enabled()],
        "locked": bool(config.access_token()),
    }


@app.get("/ready", tags=["Meta"], summary="Readiness")
async def ready() -> JSONResponse:
    """Whether this instance can actually serve traffic right now.

    Kept separate from /health on purpose. A load balancer should stop sending
    requests to an instance whose browser has gone missing, but an orchestrator
    should not restart the process for it — restarting will not install Chrome.
    """
    from . import browser
    needs_browser = any(p.needs_browser for p in reg.enabled())
    ok = browser.available() or not needs_browser
    return JSONResponse(status_code=200 if ok else 503, content={
        "status": "ready" if ok else "degraded",
        "browser_available": browser.available(),
        "browser_required": needs_browser,
    })


@app.get("/platforms", tags=["Meta"], summary="Which platforms are supported")
async def platforms() -> dict:
    return {
        "platforms": [{
            "id": p.id,
            "label": p.label,
            "hosts": sorted(p.hosts),
            "time_method": p.time_method,
            "needs_browser": p.needs_browser,
            "ready": p.ready(),
        } for p in reg.enabled()],
    }


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": config.APP_NAME,
        "version": config.APP_VERSION,
        "docs": "/docs",
        "endpoints": ["POST /v1/time", "POST /v1/within", "POST /v1/verify"],
    }
