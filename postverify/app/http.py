"""HTTP layer ke saanjhe tukde — UI endpoints aur /api/v1 dono ise use karte hain.

Alag file isliye hai taaki main.py aur api.py ek doosre ko import na karein.
"""
from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, UploadFile

from . import fetch
from .compare import ImageError
from .platforms import PlatformError, UnsupportedURLError

# Platform ki dikkat -> HTTP status
STATUS = {
    "invalid_id": 422,
    "no_media": 404,
    "not_visible": 404,
    "not_configured": 503,
    "disabled": 503,
    "upstream_error": 502,
}


def guard(token: str | None, header: str | None) -> None:
    """ACCESS_TOKEN set ho to mehenge endpoints sirf usi ke saath chalein.

    Public URL pe ye zaroori hai: har request pe browser chalta hai aur platform
    ki taraf jaata hai. Bina rok ke koi bhi aapke server se Instagram/Facebook
    hit kar sakta hai, aur block aapke IP pe aayega.

    Set na ho to service khuli rehti hai (local use ke liye theek).
    """
    expected = os.getenv("ACCESS_TOKEN")
    if not expected:
        return
    given = header or token or ""
    if not secrets.compare_digest(given, expected):
        raise HTTPException(401, {"error": "unauthorized",
                                  "message": "Sahi access token chahiye"})


def fail(exc: Exception) -> HTTPException:
    """Andar ki exception ko HTTP error me badlo, reason ke saath."""
    if isinstance(exc, UnsupportedURLError):
        return HTTPException(400, {"error": "unsupported_url", "message": str(exc)})
    if isinstance(exc, ImageError):
        return HTTPException(400, {"error": "bad_image", "message": str(exc)})
    if isinstance(exc, fetch.TooLargeError):
        return HTTPException(413, {"error": "too_large", "message": str(exc)})
    if isinstance(exc, PlatformError):
        return HTTPException(STATUS.get(exc.reason, 500), {
            "error": exc.reason, "platform": exc.platform, "message": str(exc)})
    raise exc


async def read_upload(image: UploadFile | None) -> bytes | None:
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
