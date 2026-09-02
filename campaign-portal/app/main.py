"""Campaign Portal — post submission verification.

Kaam ka silsila:

    1. Admin campaign banata hai aur uske creatives (images) upload karta hai
    2. User campaign me enroll karta hai, creative download karta hai
    3. User use apne social account pe post karta hai
    4. User apne post ka link portal me submit karta hai
    5. Portal verify karta hai:
         - post {window} ghante ke andar ki hai?      (submit ke waqt se)
         - us post me wahi image hai?
         - ye post pehle kisi ne submit to nahi ki?
       Sab pass -> approved. Warna saaf wajah ke saath rejected.

Verification postverify-api (alag service) se hoti hai, HTTP pe. Ye service
uska code copy nahi karti.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from . import worker as worker_module
from .config import settings
from .db import create_all, engine
from .engine_client import EngineError, engine_client
from .logging_setup import configure_logging
from .routers import admin, campaigns, submissions

log = logging.getLogger("portal")

_WEB = Path(__file__).parent / "web"

TAGS = [
    {"name": "Campaigns",
     "description": "Campaign banana aur uske creatives. Creative wahi image hai "
                    "jo user download karke apne account pe post karega."},
    {"name": "Submissions",
     "description": "User ka hissa — enroll karna aur apne post ka link dena. "
                    "Submission turant `pending` lautati hai; asli check peeche "
                    "chalta hai kyunki Instagram/Facebook pe ~15 second lagte hain."},
    {"name": "Admin",
     "description": "Sab submissions dekhna, manual approve/reject, aur dobara "
                    "check karwana."},
    {"name": "Meta", "description": "Health aur readiness."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()

    if settings.database_url.startswith("sqlite"):
        # Dev/test me tables seedha bana dete hain. Postgres pe Alembic chalti hai —
        # production me schema migrations se hi badalna chahiye.
        await create_all()

    stop = asyncio.Event()
    task: asyncio.Task | None = None
    if settings.worker_enabled:
        task = asyncio.create_task(worker_module.loop(stop), name="verification-worker")

    log.info("portal chalu | env=%s engine=%s window=%sh",
             settings.env, settings.engine_url, settings.submission_window_hours)
    try:
        yield
    finally:
        stop.set()
        if task:
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        await engine_client.aclose()
        await engine.dispose()
        log.info("portal band")


app = FastAPI(
    title="Campaign Portal",
    version="1.0.0",
    description=__doc__,
    openapi_tags=TAGS,
    lifespan=lifespan,
)

app.include_router(campaigns.router)
app.include_router(submissions.router)
app.include_router(admin.router)


# --- errors ek hi shape me ---------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Pydantic ke errors ko bhi wahi shape do jo baaki API deti hai.

    Pydantic ke raw errors me exception objects tak hote hain — unhe seedha
    response me daalna do wajah se galat hai: wo JSON me serialize nahi hote,
    aur andar ka dhaancha bahar leak karte hain. Isliye sirf saaf fields.
    """
    fields = [
        {
            "field": ".".join(str(part) for part in item.get("loc", [])[1:]) or "body",
            "message": str(item.get("msg", "galat value")),
            "type": str(item.get("type", "")),
        }
        for item in exc.errors()
    ]
    first = fields[0] if fields else {"field": "request", "message": "galat request"}
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


# --- meta ---------------------------------------------------------------

@app.get("/health", tags=["Meta"], summary="Service zinda hai")
async def health() -> dict:
    return {"status": "ok", "env": settings.env,
            "worker": settings.worker_enabled,
            "window_hours": settings.submission_window_hours}


@app.get("/ready", tags=["Meta"],
         summary="DB aur verification engine dono taiyar hain?")
async def ready() -> JSONResponse:
    """Health se alag: health kehta hai "process zinda hai", ready kehta hai
    "kaam kar sakta hoon". Load balancer ko ready dekhna chahiye."""
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
    """Web UI. API endpoints isse bilkul alag hain — ye unhi ko call karta hai."""
    page = _WEB / "index.html"
    if not page.exists():
        return JSONResponse({"service": "Campaign Portal", "docs": "/docs"})
    return FileResponse(page, headers={"Cache-Control": "no-cache"})
