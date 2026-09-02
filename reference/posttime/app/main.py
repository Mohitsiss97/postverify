"""PostTime — post ka URL do, upload ka time lo.

Har platform ki apni service hai, apna route:

    POST /x/resolve            GET /x/info
    POST /linkedin/resolve     GET /linkedin/info
    POST /youtube/resolve      GET /youtube/info
    POST /instagram/resolve    GET /instagram/info
    POST /facebook/resolve     GET /facebook/info

Aur agar platform pehle se pata nahi ho: POST /resolve khud detect kar leta hai.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import platforms as reg
from .platforms import (
    Platform,
    ResolutionError,
    UnsupportedURLError,
    WrongPlatformError,
)
from .service import resolve, resolve_with

_WEB = Path(__file__).parent / "web"

app = FastAPI(
    title="PostTime",
    version="2.0.0",
    description="Post URL se upload timestamp. Har platform ki apni service.",
)


# --- request/response shapes -------------------------------------------

class ResolveRequest(BaseModel):
    url: str = Field(..., description="Post ka public URL")
    tz: str | None = Field(None, examples=["Asia/Kolkata"], description="IANA timezone")


class ResolveResponse(BaseModel):
    platform: str
    platform_label: str
    post_id: str
    canonical_url: str
    published_at: datetime
    published_at_local: str | None
    timezone: str | None
    age_seconds: int
    age_human: str
    method: str
    precision: str


class BatchRequest(BaseModel):
    urls: list[str] = Field(..., max_length=100)
    tz: str | None = None


# --- error mapping ------------------------------------------------------

_STATUS = {
    "invalid_id": 422,
    "not_visible": 404,
    "not_configured": 503,
    "disabled": 503,
    "upstream_error": 502,
}


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, WrongPlatformError):
        return HTTPException(400, {
            "error": "wrong_platform",
            "message": str(exc),
            "expected": exc.expected,
            "actual": exc.actual,
        })
    if isinstance(exc, UnsupportedURLError):
        return HTTPException(400, {"error": "unsupported_url", "message": str(exc)})
    if isinstance(exc, ResolutionError):
        return HTTPException(_STATUS.get(exc.reason, 500), {
            "error": exc.reason,
            "platform": exc.platform,
            "message": str(exc),
        })
    raise exc


# --- per-platform services ---------------------------------------------

def _mount(p: Platform) -> APIRouter:
    """Ek platform ke liye uska apna router."""
    router = APIRouter(prefix=f"/{p.id}", tags=[p.label])

    @router.get("/info", summary=f"{p.label} service ki details")
    async def info() -> dict:
        return p.descriptor()

    @router.post("/resolve", response_model=ResolveResponse,
                 summary=f"{p.label} post ka timestamp")
    async def resolve_here(req: ResolveRequest) -> ResolveResponse:
        try:
            return await resolve_with(p, req.url, tz=req.tz)
        except Exception as e:
            raise _fail(e) from e

    return router


for _p in reg.enabled():
    app.include_router(_mount(_p))


# --- shared endpoints ---------------------------------------------------

@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "platforms": [p.id for p in reg.enabled()]}


@app.get("/platforms", tags=["meta"], summary="Picker isi se banta hai")
async def platform_list() -> dict:
    live = {p.id for p in reg.enabled()}
    return {
        "platforms": [p.descriptor() for p in reg.enabled()],
        "not_deployed": [
            {"id": p.id, "label": p.label} for p in reg.catalog() if p.id not in live
        ],
    }


@app.post("/resolve", response_model=ResolveResponse, tags=["meta"],
          summary="Platform khud detect karke resolve")
async def resolve_auto(req: ResolveRequest) -> ResolveResponse:
    try:
        return await resolve(req.url, tz=req.tz)
    except Exception as e:
        raise _fail(e) from e


@app.post("/resolve/batch", tags=["meta"])
async def resolve_batch(req: BatchRequest) -> dict:
    out = []
    for u in req.urls:
        try:
            out.append({"url": u, "ok": True, "data": (await resolve(u, tz=req.tz)).dict()})
        except (UnsupportedURLError, WrongPlatformError, ResolutionError) as e:
            out.append({"url": u, "ok": False,
                        "error": getattr(e, "reason", "unsupported_url"),
                        "message": str(e)})
    return {"results": out}


@app.get("/", include_in_schema=False)
async def ui():
    page = _WEB / "index.html"
    if not page.exists():
        return {"service": "PostTime", "docs": "/docs"}
    return FileResponse(page)
