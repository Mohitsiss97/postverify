"""PostVerify — ek post ka URL do (aur chaho to ek image), sab pata chal jayega.

Platform khud detect hota hai — user ko kuch chunna nahi padta.

Do kadam:

    POST /prepare   url          -> time + post ki images (humare apne origin se)
    POST /verify    session+image-> wo image is post me hai ya nahi, kitne % match

Ya ek hi call me: POST /verify url + image.

Images humare server pe download hoti hain aur /media/... se serve hoti hain,
kyunki platform ke CDN links aur embed iframes browser me aksar block ho jaate
hain. Check khatam hote hi wo saara data delete ho jaata hai.

Ye PostTime aur ImageMatch ka final roop hai. Wo dono services waise ki waisi
hain — unhe chhua nahi gaya.
"""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import fetch
from . import platforms as reg
from .compare import ImageError
from .platforms import PlatformError, UnsupportedURLError
from .service import Prepared, check, prepare, verify
from .store import store

_WEB = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Service band ho rahi hai — kuch bhi peeche na chhoote.
    store.drop_all()


app = FastAPI(
    title="PostVerify",
    version="2.0.0",
    description="Post ka URL do — upload time, aur chaho to image match bhi.",
    lifespan=lifespan,
)

_STATUS = {
    "invalid_id": 422,
    "no_media": 404,
    "not_visible": 404,
    "not_configured": 503,
    "disabled": 503,
    "upstream_error": 502,
}


def _guard(token: str | None, header: str | None) -> None:
    """ACCESS_TOKEN set ho to mehenge endpoints sirf usi ke saath chalein.

    Public URL pe ye zaroori hai: har request pe browser chalta hai aur platform
    ki taraf jaata hai. Bina rok ke koi bhi aapke server se Instagram/Facebook
    hit kar sakta hai, aur block aapke IP pe aayega — aapke kaam ka nahi.

    Set na ho to service khuli rehti hai (local use ke liye theek).
    """
    expected = os.getenv("ACCESS_TOKEN")
    if not expected:
        return
    given = header or token or ""
    if not secrets.compare_digest(given, expected):
        raise HTTPException(401, {"error": "unauthorized",
                                  "message": "Sahi access token chahiye"})


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, UnsupportedURLError):
        return HTTPException(400, {"error": "unsupported_url", "message": str(exc)})
    if isinstance(exc, ImageError):
        return HTTPException(400, {"error": "bad_image", "message": str(exc)})
    if isinstance(exc, fetch.TooLargeError):
        return HTTPException(413, {"error": "too_large", "message": str(exc)})
    if isinstance(exc, PlatformError):
        return HTTPException(_STATUS.get(exc.reason, 500), {
            "error": exc.reason, "platform": exc.platform, "message": str(exc)})
    raise exc


async def _read(image: UploadFile | None) -> bytes | None:
    """Upload sirf memory me — disk pe kabhi nahi jaati."""
    if image is None or not image.filename:
        return None
    data = await image.read()
    if not data:
        return None
    if len(data) > fetch.MAX_IMAGE_BYTES:
        raise fetch.TooLargeError(
            f"Image bahut badi hai ({len(data) // 1024 // 1024} MB). "
            f"Limit {fetch.MAX_IMAGE_BYTES // 1024 // 1024} MB hai.")
    return data


# --- step 1 -------------------------------------------------------------

@app.post("/prepare", summary="URL se time + post ki images laao")
async def prepare_post(url: str = Form(...), tz: str | None = Form(None),
                       token: str | None = Form(None),
                       x_access_token: str | None = Header(None)) -> dict:
    """Mehenga kadam yahi hai — page render/fetch aur images download.

    Iske baad /verify turant chalta hai, kyunki images pehle se maujood hain.
    """
    _guard(token, x_access_token)
    try:
        return (await prepare(url, tz=tz)).dict()
    except Exception as e:
        raise _fail(e) from e


@app.get("/media/{token}/{name}", include_in_schema=False)
async def media(token: str, name: str):
    """Download ki hui image apne origin se — CDN block ho to bhi dikh jaaye."""
    session = store.get(token)
    if session is None:
        raise HTTPException(404, {"error": "expired",
                                  "message": "Ye preview ab maujood nahi hai"})
    path = store.path(session, name)
    if path is None:
        raise HTTPException(404, {"error": "not_found", "message": "File nahi mili"})
    return FileResponse(path, headers={"Cache-Control": "no-store"})


# --- step 2 -------------------------------------------------------------

@app.post("/verify", summary="Image ko post se milao (session ya seedha URL se)")
async def verify_post(
    url: str | None = Form(None),
    session: str | None = Form(None),
    tz: str | None = Form(None),
    image: UploadFile | None = File(None),
    token: str | None = Form(None),
    x_access_token: str | None = Header(None),
) -> dict:
    """session do to turant (images pehle se hain), url do to poora kaam yahin.

    Dono soorat me kaam khatam hote hi post ki downloaded images delete ho jaati hain.
    """
    _guard(token, x_access_token)
    try:
        uploaded = await _read(image)

        if session:
            stored = store.get(session)
            if stored is None or not isinstance(stored.payload, Prepared):
                raise HTTPException(404, {
                    "error": "expired",
                    "message": "Preview ka data expire ho gaya — URL dobara daaliye"})
            if uploaded is None:
                raise HTTPException(400, {
                    "error": "bad_image", "message": "Koi image upload nahi hui"})
            return (await check(stored.payload, uploaded)).dict()

        if not url:
            raise HTTPException(400, {
                "error": "unsupported_url", "message": "url ya session dena zaroori hai"})
        return (await verify(url, uploaded, tz=tz)).dict()
    except HTTPException:
        raise
    except Exception as e:
        raise _fail(e) from e


@app.delete("/session/{token}", summary="Is session ka saara data abhi mita do")
async def drop_session(token: str) -> dict:
    return {"deleted": store.drop(token)}


# --- meta ---------------------------------------------------------------

@app.get("/platforms", tags=["meta"], summary="Kaunse platforms support hain")
async def platform_list() -> dict:
    live = {p.id for p in reg.enabled()}
    return {
        "platforms": [p.descriptor() for p in reg.enabled()],
        "not_deployed": [{"id": p.id, "label": p.label}
                         for p in reg.catalog() if p.id not in live],
    }


@app.get("/health", tags=["meta"])
async def health() -> dict:
    store.sweep()
    return {"status": "ok", "platforms": [p.id for p in reg.enabled()],
            "locked": bool(os.getenv("ACCESS_TOKEN")),
            "store": store.stats()}


@app.get("/", include_in_schema=False)
async def ui():
    page = _WEB / "index.html"
    if not page.exists():
        return {"service": "PostVerify", "docs": "/docs"}
    return FileResponse(page)
