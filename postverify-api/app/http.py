"""Shared HTTP concerns: authentication, error translation and upload reading.

These live in their own module so that the route modules never have to import
one another.
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, UploadFile

from . import config, fetch
from .compare import ImageError
from .platforms import PlatformError, UnsupportedURLError

# Platform-level failure reason -> HTTP status code.
STATUS = {
    "invalid_id": 422,
    "no_media": 404,
    "not_visible": 404,
    "not_configured": 503,
    "disabled": 503,
    "upstream_error": 502,
}


def guard(token: str | None, header: str | None) -> None:
    """Require ACCESS_TOKEN on the expensive endpoints when one is configured.

    This matters on any public deployment: every request launches a browser and
    reaches out to the platform. Left open, anyone can drive Instagram and
    Facebook traffic through your server, and the resulting block lands on your
    IP address.

    When ACCESS_TOKEN is unset the service stays open, which is convenient for
    local use and is why the deployment checklist treats setting it as
    mandatory.
    """
    expected = config.access_token()
    if not expected:
        return
    given = header or token or ""
    if not secrets.compare_digest(given, expected):
        raise HTTPException(401, {"error": "unauthorized",
                                  "message": "A valid access token is required"})


def fail(exc: Exception) -> HTTPException:
    """Translate an internal exception into an HTTP error that states the cause."""
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
    """Read an uploaded image into memory. It is never written to disk."""
    if image is None or not image.filename:
        return None
    data = await image.read()
    if not data:
        return None
    if len(data) > fetch.MAX_IMAGE_BYTES:
        raise fetch.TooLargeError(
            f"The image is too large ({len(data) // 1024 // 1024} MB). "
            f"The limit is {fetch.MAX_IMAGE_BYTES // 1024 // 1024} MB.")
    return data
