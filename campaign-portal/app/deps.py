"""Shared dependencies — user pehchan, admin guard, aur ek jaisa error shape.

Auth abhi nahi hai. Par user ki pehchan aur admin ka guard **ek hi jagah** se
aate hain, to jab JWT lagana ho tab sirf ye do function badalne padenge —
routers ko haath nahi lagana padega.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import settings


def http_error(code: int, error: str, message: str, **extra) -> HTTPException:
    """Poore portal me errors ka ek hi shape: {"error": ..., "message": ...}"""
    return HTTPException(code, {"error": error, "message": message, **extra})


def not_found(what: str) -> HTTPException:
    return http_error(status.HTTP_404_NOT_FOUND, "not_found", f"{what} nahi mila")


async def current_user(x_user_id: str | None = Header(None)) -> str:
    """Abhi user header se aata hai.

    JWT lagane pe sirf yahi function badlega — token decode karke wahi user id
    return kar dena, baaki poora portal waise ka waisa chalega.
    """
    if not x_user_id or not x_user_id.strip():
        raise http_error(
            status.HTTP_401_UNAUTHORIZED, "no_user",
            f"{settings.user_header} header dena zaroori hai")
    return x_user_id.strip()


async def require_admin(x_admin_token: str | None = Header(None)) -> None:
    """ADMIN_TOKEN set ho to admin endpoints uske bina nahi chalenge.

    Set na ho to khule hain — dev ke liye theek, par production me zaroor set
    kijiye: yahan se campaigns bante hain aur submissions override hoti hain.
    """
    expected = settings.admin_token
    if not expected:
        return
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise http_error(status.HTTP_401_UNAUTHORIZED, "unauthorized",
                         "Admin token chahiye (X-Admin-Token header)")
