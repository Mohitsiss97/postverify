"""PostVerify API — teen endpoints, aur kuch nahi.

    POST /v1/time      URL do          -> post kab upload hua
    POST /v1/within    URL + windows   -> post 1d/3d/7d/15d/1m ke andar ka hai ya nahi
    POST /v1/verify    URL + image     -> wo image us post me hai ya nahi, kitne % match

Har endpoint ka GET version bhi hai (verify chhodkar, usme file jaati hai).

Yahan koi web page nahi, koi session nahi, koi temp file nahi. Post ki images
sirf request ki memory me aati hain aur wahin khatam ho jaati hain.
"""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import (FastAPI, File, Form, Header, HTTPException, Query,
                     UploadFile)
from pydantic import BaseModel, Field

from . import fetch, window
from . import platforms as reg
from .http import fail, guard, read_upload
from .service import Result, resolve

TAGS = [
    {
        "name": "Time",
        "description": "Post kab upload hua. Sirf yahi chahiye to images download "
                       "hoti hi nahi — X aur LinkedIn pe ek bhi network call nahi jaati.",
    },
    {
        "name": "Window",
        "description": "Post kitna taaza hai. `within` me comma se alag karke jitni "
                       "windows chahiye do — `1d,3d,7d,15d,1m` — har ek ka seedha "
                       "true/false milega. Dhyan: yahan `m` = month hai, minute nahi "
                       "(minute ke liye `min`).",
    },
    {
        "name": "Image",
        "description": "Di hui image us post me hai ya nahi. Resize, crop, compress, "
                       "watermark ke baad bhi pehchan leta hai.",
    },
    {"name": "Meta", "description": "Service zinda hai, aur kya support karti hai."},
]

app = FastAPI(
    title="PostVerify API",
    version="1.0.0",
    description=(
        "Social media post ka URL do — upload time, freshness check, aur image match. "
        "Platform khud pehchana jata hai: X, Instagram, Facebook, LinkedIn, YouTube. "
        "Koi login, koi API key nahi."
    ),
    openapi_tags=TAGS,
    docs_url="/docs",
    redoc_url=None,
)


# --- request models -----------------------------------------------------

_URL = Field(..., examples=["https://www.instagram.com/p/DceLPdrCR3L/"],
             description="Kisi bhi supported platform ka post URL")
_TZ = Field(None, examples=["Asia/Kolkata"],
            description="IANA timezone — local time chahiye to")
_TOKEN = Field(None, description="ACCESS_TOKEN set ho to zaroori "
                                 "(ya X-Access-Token header)")
_WITHIN = Field(..., examples=["1d,3d,7d,15d,1m"],
                description="Comma separated windows. "
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


# --- shaping ------------------------------------------------------------

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
        # Time hi nahi mila to window ka jawab dena jhooth hoga — false padha jaata
        # "purana hai" ki tarah.
        body["within"] = None
        body["within_error"] = "upload time nahi mila, window check nahi ho sakta"
        return body

    checked = window.evaluate(windows, result.time.published_at)
    body["within"] = checked["results"]
    body["within_detail"] = checked["windows"]
    body["checked_at"] = checked["checked_at"]
    if len(windows) == 1:
        # Ek hi window ho to seedha boolean bhi — nested object khodna na pade
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
            # tier "post" = pakka isi post ki image; "page" = post page pe mili
            # (carousel slide ya related post ho sakti hai)
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

@app.post("/v1/time", tags=["Time"], summary="Post kab upload hua")
async def time_post(body: TimeIn,
                    x_access_token: str | None = Header(None)) -> dict:
    guard(body.token, x_access_token)
    return _base(await _run(body.url, tz=body.tz))


@app.get("/v1/time", tags=["Time"], summary="Wahi cheez, query params se")
async def time_get(url: str = Query(..., description="Post ka URL"),
                   tz: str | None = Query(None),
                   token: str | None = Query(None),
                   x_access_token: str | None = Header(None)) -> dict:
    guard(token, x_access_token)
    return _base(await _run(url, tz=tz))


# --- 2. within ----------------------------------------------------------

@app.post("/v1/within", tags=["Window"],
          summary="Post 1d / 3d / 7d / 15d / 1m ke andar ka hai ya nahi")
async def within_post(body: WithinIn,
                      x_access_token: str | None = Header(None)) -> dict:
    guard(body.token, x_access_token)
    windows = _windows_or_400(body.within)      # galat window pe kaam shuru hi na ho
    result = await _run(body.url, tz=body.tz)
    return _add_within(_base(result), result, windows)


@app.get("/v1/within", tags=["Window"], summary="Wahi cheez, query params se")
async def within_get(url: str = Query(..., description="Post ka URL"),
                     within: str = Query(..., examples=["1d,3d,7d,15d,1m"]),
                     tz: str | None = Query(None),
                     token: str | None = Query(None),
                     x_access_token: str | None = Header(None)) -> dict:
    guard(token, x_access_token)
    windows = _windows_or_400(within)
    result = await _run(url, tz=tz)
    return _add_within(_base(result), result, windows)


# --- 3. verify ----------------------------------------------------------

@app.post("/v1/verify", tags=["Image"], summary="Ye image us post me hai ya nahi")
async def verify_post(
    url: str = Form(..., description="Post ka URL"),
    image: UploadFile | None = File(None, description="Milani wali image"),
    image_url: str | None = Form(
        None, description="File ki jagah image ka URL — server-to-server ke liye"),
    within: str | None = Form(
        None, examples=["7d"],
        description="Optional. Yahan de doge to alag /v1/within call nahi karni "
                    "padegi — Instagram/Facebook pe wo dobara render se bacha leta hai."),
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
                "message": f"image_url se image nahi mili: {e}"}) from e
    if uploaded is None:
        raise HTTPException(400, {
            "error": "bad_image", "message": "image ya image_url dena zaroori hai"})

    result = await _run(url, tz=tz, uploaded=uploaded)
    return _add_image(_add_within(_base(result), result, windows), result)


# --- meta ---------------------------------------------------------------

@app.get("/health", tags=["Meta"], summary="Service zinda hai")
async def health() -> dict:
    return {
        "status": "ok",
        "platforms": [p.id for p in reg.enabled()],
        "locked": bool(os.getenv("ACCESS_TOKEN")),
    }


@app.get("/platforms", tags=["Meta"], summary="Kaunse platforms support hain")
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
        "service": "PostVerify API",
        "docs": "/docs",
        "endpoints": ["POST /v1/time", "POST /v1/within", "POST /v1/verify"],
    }
