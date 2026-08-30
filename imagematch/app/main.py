"""ImageMatch — ek image do aur ek post ka URL do, batayenge ki wo image us post me hai ya nahi.

Har platform ki apni service, apna route:

    POST /x/match            GET /x/info
    POST /instagram/match    GET /instagram/info
    POST /facebook/match     GET /facebook/info
    POST /linkedin/match     GET /linkedin/info
    POST /youtube/match      GET /youtube/info

Ye PostTime se bilkul alag service hai — usko chhua nahi gaya.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile

from . import fetch
from . import media as reg
from .compare import ImageError
from .media import (
    ExtractionError,
    Source,
    UnsupportedURLError,
    WrongPlatformError,
)
from .service import match, match_with

_WEB = Path(__file__).parent / "web"

app = FastAPI(
    title="ImageMatch",
    version="1.0.0",
    description="Image upload karo aur post ka URL do — batayenge wo image us post me hai ya nahi.",
)

_STATUS = {
    "no_media": 404,
    "not_visible": 404,
    "not_configured": 503,
    "disabled": 503,
    "upstream_error": 502,
}


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, WrongPlatformError):
        return HTTPException(400, {"error": "wrong_platform", "message": str(exc),
                                   "expected": exc.expected, "actual": exc.actual})
    if isinstance(exc, UnsupportedURLError):
        return HTTPException(400, {"error": "unsupported_url", "message": str(exc)})
    if isinstance(exc, ImageError):
        return HTTPException(400, {"error": "bad_image", "message": str(exc)})
    if isinstance(exc, fetch.TooLargeError):
        return HTTPException(413, {"error": "too_large", "message": str(exc)})
    if isinstance(exc, ExtractionError):
        return HTTPException(_STATUS.get(exc.reason, 500), {
            "error": exc.reason, "platform": exc.platform, "message": str(exc)})
    raise exc


async def _read(image: UploadFile) -> bytes:
    data = await image.read()
    if not data:
        raise ImageError("Koi image upload nahi hui")
    if len(data) > fetch.MAX_IMAGE_BYTES:
        raise fetch.TooLargeError(
            f"Image bahut badi hai ({len(data) // 1024 // 1024} MB). "
            f"Limit {fetch.MAX_IMAGE_BYTES // 1024 // 1024} MB hai.")
    return data


def _mount(source: Source) -> APIRouter:
    router = APIRouter(prefix=f"/{source.id}", tags=[source.label])

    @router.get("/info", summary=f"{source.label} service ki details")
    async def info() -> dict:
        return source.descriptor()

    @router.post("/match", summary=f"Image ko {source.label} post se milao")
    async def match_here(url: str = Form(...), image: UploadFile = File(...)) -> dict:
        try:
            return (await match_with(source, url, await _read(image))).dict()
        except Exception as e:
            raise _fail(e) from e

    return router


for _s in reg.enabled():
    app.include_router(_mount(_s))


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "platforms": [s.id for s in reg.enabled()]}


@app.get("/platforms", tags=["meta"], summary="Picker isi se banta hai")
async def platform_list() -> dict:
    live = {s.id for s in reg.enabled()}
    return {
        "platforms": [s.descriptor() for s in reg.enabled()],
        "not_deployed": [{"id": s.id, "label": s.label}
                         for s in reg.catalog() if s.id not in live],
    }


@app.post("/match", tags=["meta"], summary="Platform khud detect karke milao")
async def match_auto(url: str = Form(...), image: UploadFile = File(...)) -> dict:
    try:
        return (await match(url, await _read(image))).dict()
    except Exception as e:
        raise _fail(e) from e


@app.get("/", include_in_schema=False)
async def ui():
    page = _WEB / "index.html"
    if not page.exists():
        return {"service": "ImageMatch", "docs": "/docs"}
    from fastapi.responses import FileResponse
    return FileResponse(page)
