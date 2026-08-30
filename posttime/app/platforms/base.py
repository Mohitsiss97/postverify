"""Har platform ke liye ek common shape.

Ek platform service teen cheezein janti hai:
  1. kaunse URL uske hain          -> match()
  2. us URL se timestamp kaise aata hai -> timing()
  3. chalne ke liye kya chahiye     -> setup / ready()

Isse naya platform add karna = ek nayi file, aur kahin kuch touch nahi.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import ParseResult


class UnsupportedURLError(ValueError):
    """URL kisi bhi known platform se match nahi hua."""


class WrongPlatformError(ValueError):
    """User ne platform A chuna, par URL platform B ka hai."""

    def __init__(self, expected: str, actual: str, url: str,
                 expected_label: str | None = None, actual_label: str | None = None):
        super().__init__(
            f"Ye {actual_label or actual} ka link hai, "
            f"aapne {expected_label or expected} chuna hai")
        self.expected = expected
        self.actual = actual
        self.url = url


class ResolutionError(RuntimeError):
    """Platform sahi hai, par timestamp nikal nahi paye."""

    def __init__(self, message: str, *, platform: str, reason: str):
        super().__init__(message)
        self.platform = platform
        self.reason = reason


@dataclass(frozen=True)
class Match:
    """URL parse ho gaya."""
    post_id: str
    canonical_url: str
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Timing:
    """Timestamp mil gaya."""
    published_at: datetime      # hamesha UTC
    method: str                 # "id-embedded" | "api"
    precision: str              # "millisecond" | "second"


class Platform:
    """Base class — har platform file isko extend karti hai."""

    id: str = ""
    label: str = ""
    hosts: frozenset[str] = frozenset()

    sample_url: str = ""
    method: str = ""
    precision: str = ""
    how: str = ""                    # ek line: kaam kaise karta hai
    setup: str | None = None         # env var jo chahiye; None = kuch nahi chahiye
    setup_optional: bool = False     # key ho to behtar, par bina uske bhi chalta hai
    setup_note: str = ""             # user ko setup ke baare me kya batayein

    # -- URL --------------------------------------------------------------
    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        """URL isi platform ka hai to Match, warna None."""
        raise NotImplementedError

    # -- timestamp --------------------------------------------------------
    async def timing(self, post_id: str, extra: dict) -> Timing:
        raise NotImplementedError

    # -- config -----------------------------------------------------------
    def ready(self) -> bool:
        """Ye service abhi chal sakti hai ya nahi."""
        if self.setup is None or self.setup_optional:
            return True
        return bool(os.getenv(self.setup))

    def configured(self) -> bool:
        """Iska optional key/token set hai ya nahi."""
        return self.setup is not None and bool(os.getenv(self.setup))

    def descriptor(self) -> dict:
        """Jo /platforms return karta hai — UI ka picker isi se banta hai."""
        return {
            "id": self.id,
            "label": self.label,
            "sample_url": self.sample_url,
            "method": self.method,
            "precision": self.precision,
            "how": self.how,
            "needs_setup": self.setup is not None and not self.setup_optional,
            "setup_optional": self.setup_optional,
            "configured": self.configured(),
            "setup_env": self.setup,
            "setup_note": self.setup_note,
            "ready": self.ready(),
            "endpoint": f"/{self.id}/resolve",
        }
