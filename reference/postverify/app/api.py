"""Integration API — /api/v1/*

UI waale endpoints (/prepare, /verify) browser ke liye bane hain: session,
/media se images, do kadam. Wo integration ke liye asuvidhajanak hain.

Yahan sab kuch **ek call me** hota hai aur jawab machine-friendly hai:

    POST /api/v1/time      URL do -> upload time (+ chaho to window check)
    POST /api/v1/verify    URL + image do -> time + match + score
    GET  /api/v1/time      wahi, query params se (testing ke liye aasan)

`within` sabse kaam ka parameter hai: `within=1d,3d,7d,1m` bhejo aur har window
ka seedha true/false milega. Yahan m = month hai, minute nahi (`min` likhiye
minute ke liye).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import (APIRouter, File, Form, Header, HTTPException, Query,
                     UploadFile)
from pydantic import BaseModel, Field

from . import fetch, window
from .http import fail, guard, read_upload
from .service import Verification, verify

router = APIRouter(prefix="/api/v1", tags=["Integration API"])


class TimeRequest(BaseModel):
    url: str = Field(..., examples=["https://www.instagram.com/p/DceLPdrCR3L/"])
    tz: str | None = Field(None, examples=["Asia/Kolkata"],
                           description="IANA timezone, local time ke liye")
    within: str | None = Field(
        None, examples=["1d,3d,7d,1m"],
        description="Comma separated windows. Units: s, min, h, d, w, m/mo (month), y")
    token: str | None = Field(
        None, description="ACCESS_TOKEN set ho to zaroori (ya X-Access-Token header)")


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _windows_or_400(raw: str | None) -> list[window.Window]:
    try:
        return window.parse(raw)
    except window.WindowError as e:
        raise HTTPException(400, {"error": "bad_window", "message": str(e)}) from e


def _shape(result: Verification, windows: list[window.Window]) -> dict:
    """Verification ko stable API shape me badlo."""
    body: dict = {
        "ok": True,
        "platform": result.platform,
        "platform_label": result.platform_label,
        "post_id": result.post_id,
        "canonical_url": result.canonical_url,
        "time": None,
        "summary": result.summary,
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

    if windows:
        if result.time is None:
            # Time hi nahi mila to window ka jawab dena jhooth hoga.
            body["within"] = None
            body["within_error"] = "upload time nahi mila, window check nahi ho sakta"
        else:
            checked = window.evaluate(windows, result.time.published_at)
            body["within"] = checked["results"]
            body["within_detail"] = checked["windows"]
            body["checked_at"] = checked["checked_at"]
            if len(windows) == 1:
                # Ek hi window ho to seedha boolean bhi de do — integration aasan
                body["is_within"] = checked["results"][windows[0].label]

    if result.image_checked:
        image: dict = {
            "checked": True,
            "present": result.present,
            "verdict": result.verdict,
            "score": result.score,
            "images_checked": result.images_checked,
        }
        if result.image_error:
            image.update({"checked": False, "error": result.image_error})
        if result.matched:
            m = result.matched
            image["matched"] = {
                # tier "post" = pakka isi post ki image; "page" = post page pe mili
                # (carousel slide ya related post ho sakti hai)
                "tier": m.tier,
                "score": m.score,
                "orb_inliers": m.orb_inliers,
                "phash_distance": m.phash_distance,
                "note": m.note,
            }
        body["image"] = image

    return body


async def _run(url: str, uploaded: bytes | None, tz: str | None,
               within: str | None) -> dict:
    windows = _windows_or_400(within)
    result = await verify(url, uploaded, tz=tz)
    return _shape(result, windows)


# --- time only ----------------------------------------------------------

@router.post("/time", summary="URL se upload time (+ window check)")
async def api_time(req: TimeRequest,
                   x_access_token: str | None = Header(None)) -> dict:
    """Sirf time chahiye to images download hoti hi nahi.

    X aur LinkedIn pe to ek bhi network call nahi jaati — unka time ID ke
    andar hota hai.
    """
    guard(req.token, x_access_token)
    try:
        return await _run(req.url, None, req.tz, req.within)
    except HTTPException:
        raise
    except Exception as e:
        raise fail(e) from e


@router.get("/time", summary="Wahi cheez, query params se")
async def api_time_get(
    url: str = Query(..., description="Post ka URL"),
    tz: str | None = Query(None),
    within: str | None = Query(None, examples=["1d,3d,7d,1m"]),
    token: str | None = Query(None),
    x_access_token: str | None = Header(None),
) -> dict:
    guard(token, x_access_token)
    try:
        return await _run(url, None, tz, within)
    except HTTPException:
        raise
    except Exception as e:
        raise fail(e) from e


# --- time + image match -------------------------------------------------

@router.post("/verify", summary="URL + image -> time, match aur score")
async def api_verify(
    url: str = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(
        None, description="File upload ki jagah image ka URL bhi de sakte ho"),
    tz: str | None = Form(None),
    within: str | None = Form(None, examples=["1d,7d"]),
    token: str | None = Form(None),
    x_access_token: str | None = Header(None),
) -> dict:
    """Image do file upload se, ya `image_url` se (server-to-server ke liye aasan).

    Post ki images download hoti hain, compare hoti hain, aur jawab jaate hi
    delete ho jaati hain.
    """
    guard(token, x_access_token)
    try:
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

        return await _run(url, uploaded, tz, within)
    except HTTPException:
        raise
    except Exception as e:
        raise fail(e) from e
