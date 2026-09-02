"""Time windows: "was this post published within the last 7 days?"

For integrators this is usually the question that matters. The timestamp is
easy enough to obtain, but the decision almost always depends on how recent the
post is, so the `within` parameter accepts one or more windows and answers each
one directly:

    within=1d,3d,7d,1m  ->  {"1d": false, "3d": false, "7d": true, "1m": true}

One deliberate departure from convention: **`m` means month here, not minute.**
Many parsers read `m` as minute, but the requirement this was built for was
"1 month", and silently answering a different question is worse than being
unconventional. Use `min` for minutes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Seconds per unit.
_UNITS = {
    "s": 1,
    "sec": 1,
    "min": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "d": 86_400,
    "day": 86_400,
    "w": 604_800,
    "week": 604_800,
    "m": 2_592_000,        # 30 days — not a calendar month
    "mo": 2_592_000,
    "month": 2_592_000,
    "y": 31_536_000,       # 365 days
    "year": 31_536_000,
}

_TOKEN = re.compile(r"^(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>[a-z]*)$")

MAX_WINDOWS = 10


class WindowError(ValueError):
    """The window string could not be understood."""


@dataclass(frozen=True)
class Window:
    label: str          # exactly as written by the caller; becomes the response key
    seconds: int

    def contains(self, age_seconds: int) -> bool:
        # Timestamps in the future (clock skew) count as inside the window.
        return age_seconds <= self.seconds


def parse_one(raw: str) -> Window:
    text = (raw or "").strip().lower()
    if not text:
        raise WindowError("empty window")

    m = _TOKEN.match(text)
    if not m:
        raise WindowError(
            f"Could not understand '{raw}'. Write it like: 1d, 3d, 7d, 1w, 1m, 24h")

    unit = m.group("unit") or "d"          # a bare number means days
    if unit not in _UNITS:
        raise WindowError(
            f"Unknown unit '{unit}' in '{raw}'. Valid units: "
            f"s, min, h, d, w, m/mo (month), y")

    seconds = float(m.group("n")) * _UNITS[unit]
    if seconds <= 0:
        raise WindowError(f"'{raw}' is zero or negative")
    return Window(label=raw.strip(), seconds=int(seconds))


def parse(raw: str | None) -> list[Window]:
    """Parse a comma-separated list of windows. None or empty gives an empty list."""
    if not raw or not raw.strip():
        return []
    parts = [p for p in (piece.strip() for piece in raw.split(",")) if p]
    if len(parts) > MAX_WINDOWS:
        raise WindowError(f"No more than {MAX_WINDOWS} windows per request")

    seen: set[str] = set()
    out: list[Window] = []
    for part in parts:
        window = parse_one(part)
        if window.label in seen:
            continue
        seen.add(window.label)
        out.append(window)
    return out


def evaluate(windows: list[Window], published_at: datetime,
             now: datetime | None = None) -> dict:
    """Answer each window, and show the arithmetic behind every answer."""
    now = now or datetime.now(timezone.utc)
    age = int((now - published_at).total_seconds())
    return {
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "age_seconds": age,
        "results": {w.label: w.contains(age) for w in windows},
        "windows": {w.label: {
            "seconds": w.seconds,
            "cutoff": (now - timedelta(seconds=w.seconds))
                      .isoformat().replace("+00:00", "Z"),
            "within": w.contains(age),
        } for w in windows},
    }
