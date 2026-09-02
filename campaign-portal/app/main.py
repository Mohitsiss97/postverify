"""Campaign Portal — verification of participant post submissions.

How the work flows:

    1. An administrator creates a campaign and uploads its creatives.
    2. A participant joins the campaign and downloads a creative.
    3. They post it to their own social media account.
    4. They submit the link to that post here.
    5. The portal verifies three things:
         - was the post published within the campaign's window, counted from
           the moment of submission?
         - does the post contain that same image?
         - has this post already been submitted by someone?
       All three pass and the submission is approved. Otherwise it is rejected
       with a reason the participant can act on.

Verification is performed by postverify-api, a separate service, over HTTP. The
portal does not contain a copy of its code.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import worker as worker_module
from .config import settings
from .db import create_all, engine
from .engine_client import EngineError, engine_client
from .logging_setup import configure_logging
from .middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from .routers import admin, campaigns, submissions

log = logging.getLogger("portal")

_WEB = Path(__file__).parent / "web"

TAGS = [
    {"name": "Campaigns",
     "description": "Creating campaigns and their creatives. A creative is the "
                    "image a participant downloads and posts to their own "
                    "account."},
    {"name": "Submissions",
     "description": "The participant's side: joining a campaign and submitting a "
                    "post link. A submission returns `pending` immediately; the "
                    "check itself runs in the background, because opening a post "
                    "on Instagram or Facebook takes around 15 seconds."},
    {"name": "Admin",
     "description": "Reviewing every submission, approving or rejecting one by "
                    "hand, and requeueing one for another check."},
    {"name": "Meta", "description": "Liveness and readiness."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()

    # Fail fast: a misconfigured instance should never accept its first request.
    warnings = settings.validate_for_start()
    log.info("starting Campaign Portal", extra={"config": settings.describe()})
    for warning in warnings:
        log.warning(warning)

    if settings.database_url.startswith("sqlite"):
        # Tables are created directly for development and tests. On PostgreSQL
        # the schema is owned by Alembic; production must migrate, never
        # create_all, or the migration history and the live schema diverge.
        await create_all()

    stop = asyncio.Event()
    task: asyncio.Task | None = None
    if settings.worker_enabled:
        task = asyncio.create_task(worker_module.loop(stop), name="verification-worker")

    try:
        yield
    finally:
        # Ask the worker to stop and give it time to finish the submission it is
        # holding, so a deployment does not strand one mid-verification.
        stop.set()
        if task:
            try:
                await asyncio.wait_for(task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
        await engine_client.aclose()
        await engine.dispose()
        log.info("Campaign Portal stopped")


app = FastAPI(
    title="Campaign Portal",
    version="1.0.0",
    description=__doc__,
    openapi_tags=TAGS,
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", settings.user_header, "X-Admin-Token",
                       "X-Request-ID"],
        expose_headers=["X-Total-Count", "X-Request-ID"],
    )

app.include_router(campaigns.router)
app.include_router(submissions.router)
app.include_router(admin.router)


# --- one error shape ----------------------------------------------------
#
# Every error from the portal looks like this:
#     {"error": "<code>", "message": "<human readable>"}
#
# FastAPI wraps HTTPException in {"detail": ...} of its own accord, which
# produced two different shapes — validation errors flat, everything else
# nested — and forced clients to write two parsers. These handlers unwrap it
# into a single shape.

# Codes and messages for the framework's own errors (404, 405, and so on).
_FALLBACK = {
    400: ("bad_request", "The request could not be understood"),
    401: ("unauthorized", "This requires identification"),
    403: ("forbidden", "This is not allowed"),
    404: ("not_found", "No such resource"),
    405: ("method_not_allowed", "That method is not supported on this path"),
    409: ("conflict", "This cannot be done right now"),
    413: ("too_large", "The payload is too large"),
    415: ("unsupported_media_type", "That file type is not accepted"),
    429: ("rate_limited", "Too many requests — please try again shortly"),
    500: ("server_error", "Something went wrong on our side"),
    503: ("unavailable", "The service is temporarily unavailable"),
}


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        content = dict(detail)                      # one we raised ourselves
    else:
        # One of FastAPI's or Starlette's own errors (404 "Not Found", 405, ...)
        code, message = _FALLBACK.get(
            exc.status_code, ("http_error", "The request could not be completed"))
        content = {"error": code, "message": message}
    return JSONResponse(status_code=exc.status_code, content=content,
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Give Pydantic's errors the same shape as the rest of the API.

    Pydantic's raw errors can contain exception objects. Putting those straight
    into a response is wrong twice over: they are not JSON-serialisable, and
    they leak internal structure. Hence only the clean fields.
    """
    fields = [
        {
            "field": ".".join(str(part) for part in item.get("loc", [])[1:]) or "body",
            "message": str(item.get("msg", "invalid value")),
            "type": str(item.get("type", "")),
        }
        for item in exc.errors()
    ]
    first = fields[0] if fields else {"field": "request", "message": "invalid request"}
    return JSONResponse(
        status_code=422,
        content={"error": "invalid_request",
                 "message": f"{first['field']}: {first['message']}",
                 "fields": fields},
    )


@app.exception_handler(EngineError)
async def engine_error(_: Request, exc: EngineError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"error": "engine_unavailable", "message": exc.message},
    )


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
        "error": "server_error",
        "message": "Something went wrong on our side",
        "request_id": request_id,
    })


# --- meta ---------------------------------------------------------------

@app.get("/health", tags=["Meta"], summary="Liveness")
async def health() -> dict:
    return {"status": "ok", "env": settings.env,
            "worker": settings.worker_enabled,
            "window_hours": settings.submission_window_hours}


@app.get("/ready", tags=["Meta"],
         summary="Are the database and the verification engine both usable?")
async def ready() -> JSONResponse:
    """Separate from /health on purpose.

    Health answers "the process is alive"; readiness answers "it can do the
    work". A load balancer should route on readiness, while an orchestrator
    should restart on liveness — restarting the portal will not bring the engine
    or the database back.
    """
    checks: dict[str, object] = {}

    try:
        from sqlalchemy import text

        from .db import SessionLocal
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"fail: {e}"

    try:
        info = await engine_client.health()
        checks["engine"] = "ok"
        checks["engine_platforms"] = info.get("platforms", [])
    except EngineError as e:
        checks["engine"] = f"fail: {e.message}"

    healthy = checks.get("database") == "ok" and checks.get("engine") == "ok"
    return JSONResponse(
        status_code=200 if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"ready": healthy, "checks": checks},
    )


@app.get("/", include_in_schema=False)
async def ui():
    """The web UI. It is a client of the API below, with no privileges of its own."""
    page = _WEB / "index.html"
    if not page.exists():
        return JSONResponse({"service": "Campaign Portal", "docs": "/docs"})
    return FileResponse(page, headers={"Cache-Control": "no-cache"})
