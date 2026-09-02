"""Shared dependencies: user identity, the admin guard, and one error shape.

There is no authentication yet. But user identity and the admin guard both come
from **one place**, so introducing JWT later means changing these two functions
and nothing in the routers.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import settings


def http_error(code: int, error: str, message: str, **extra) -> HTTPException:
    """One error shape across the whole portal: {"error": ..., "message": ...}"""
    return HTTPException(code, {"error": error, "message": message, **extra})


def not_found(what: str) -> HTTPException:
    return http_error(status.HTTP_404_NOT_FOUND, "not_found", f"{what} was not found")


async def current_user(x_user_id: str | None = Header(None)) -> str:
    """For now the user is identified by a header.

    Introducing JWT changes only this function: decode the token and return the
    same user ID, and the rest of the portal is unaffected.
    """
    if not x_user_id or not x_user_id.strip():
        raise http_error(
            status.HTTP_401_UNAUTHORIZED, "no_user",
            f"The {settings.user_header} header is required")
    return x_user_id.strip()


async def require_admin(x_admin_token: str | None = Header(None)) -> None:
    """When ADMIN_TOKEN is set, the admin endpoints require it.

    When it is unset they are open, which is fine for development. In production
    it must be set: these endpoints create campaigns and override submission
    decisions.
    """
    expected = settings.admin_token
    if not expected:
        return
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise http_error(status.HTTP_401_UNAUTHORIZED, "unauthorized",
                         "An admin token is required (X-Admin-Token header)")
