"""Post se images nikalne ka common shape.

Har platform ki apni file hai, PostTime service jaisa hi pattern. Farq itna hai
ki yahan timestamp nahi, image URLs nikalte hain.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
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


class ExtractionError(RuntimeError):
    """Platform sahi hai, par images nikal nahi paye."""

    def __init__(self, message: str, *, platform: str, reason: str):
        super().__init__(message)
        self.platform = platform
        self.reason = reason


@dataclass(frozen=True)
class ImageRef:
    """Post pe mili ek image.

    tier batata hai ki ye kitni pakki hai:
      "post"  — og:image, yaani pakka isi post ki hai
      "page"  — post ke page pe mili; carousel ki doosri slide ho sakti hai,
                ya "more posts" waali koi aur image. Isliye result me alag
                se batate hain, taaki jhoothi pakkiyat na ho.
    """
    url: str
    tier: str = "post"
    label: str = ""
    group: str = ""     # ek hi image ke alag resolutions ka group; pehla jo
                        # download ho jaye wahi use hota hai


@dataclass(frozen=True)
class Match:
    post_id: str
    canonical_url: str
    render_url: str


class Source:
    """Ek platform ka image extractor."""

    id: str = ""
    label: str = ""
    hosts: frozenset[str] = frozenset()
    sample_url: str = ""
    how: str = ""
    needs_browser: bool = False

    def match(self, url: str, parts: ParseResult, host: str) -> Match | None:
        raise NotImplementedError

    async def images(self, match: Match) -> list[ImageRef]:
        raise NotImplementedError

    def ready(self) -> bool:
        if not self.needs_browser:
            return True
        from .. import browser
        return browser.available()

    def descriptor(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "sample_url": self.sample_url,
            "how": self.how,
            "needs_browser": self.needs_browser,
            "ready": self.ready(),
            "endpoint": f"/{self.id}/match",
        }
