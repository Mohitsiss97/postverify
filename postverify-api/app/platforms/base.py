"""One platform, three responsibilities.

Timing and image extraction were separate concerns in the earlier reference
services. They are unified here because the URL parsing was identical in both
and both rendered the same page — keeping them apart meant launching a browser
twice for a single post.

Every platform answers:
    match()         is this URL mine, and what is the post ID?
    published_at()  when was this post created?
    images()        which images does this post carry?
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import ParseResult


class UnsupportedURLError(ValueError):
    """The URL did not match any known platform."""


class PlatformError(RuntimeError):
    """The platform was recognised, but the work could not be completed."""

    def __init__(self, message: str, *, platform: str, reason: str):
        super().__init__(message)
        self.platform = platform
        self.reason = reason


@dataclass(frozen=True)
class Match:
    post_id: str
    canonical_url: str
    render_url: str
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Timing:
    published_at: datetime      # always UTC
    method: str                 # id-embedded | public-page | headless-page | api
    precision: str              # millisecond | second


@dataclass(frozen=True)
class ImageRef:
    """An image found on the post.

    tier:
      "post"  — taken from og:image, so it definitively belongs to this post
      "page"  — found on the post's page; may be a carousel slide or a related post
    """
    url: str
    tier: str = "post"
    label: str = ""
    group: str = ""             # different resolutions of the same image


class Platform:
    id: str = ""
    label: str = ""
    hosts: frozenset[str] = frozenset()
    sample_url: str = ""

    time_method: str = ""
    time_note: str = ""
    image_note: str = ""
    needs_browser: bool = False
    optional_env: str | None = None      # improves results, but is not required

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        raise NotImplementedError

    async def load(self, match: Match) -> dict:
        """The one expensive step: render or fetch the page.

        Both the timestamp and the images are derived from this single result,
        which is why a post is only ever rendered once, not twice.
        """
        return {}

    async def published_at(self, match: Match, ctx: dict) -> Timing:
        raise NotImplementedError

    async def images(self, match: Match, ctx: dict) -> list[ImageRef]:
        raise NotImplementedError

    # -- configuration ---------------------------------------------------

    def configured(self) -> bool:
        return self.optional_env is not None and bool(os.getenv(self.optional_env))

    def ready(self) -> bool:
        if not self.needs_browser:
            return True
        from .. import browser
        return browser.available()

    def descriptor(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "hosts": sorted(self.hosts),      # so clients can show which hosts are live
            "sample_url": self.sample_url,
            "time_method": self.time_method,
            "time_note": self.time_note,
            "image_note": self.image_note,
            "needs_browser": self.needs_browser,
            "ready": self.ready(),
        }
