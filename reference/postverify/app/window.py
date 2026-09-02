"""Time windows: "ye post 7 din ke andar ka hai kya?"

Integration ke liye sabse kaam ki cheez yahi hai — timestamp to mil jaata hai, par
aksar sawaal ye hota hai ki post kitna taaza hai. Isliye `within` parameter ek ya
kai windows leta hai aur har ek ka seedha true/false deta hai:

    within=1d,3d,7d,1m  ->  {"1d": false, "3d": false, "7d": true, "1m": true}

Ek dhyan dene layak baat: yahan **m ka matlab month hai, minute nahi**. Bahut
parsers me m = minute hota hai, isliye ye jaan-boojh kar alag hai — sawaal hi
"1 month" waala tha. Minute chahiye to `min` likhiye.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Kitne seconds ka ek unit
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
    "m": 2_592_000,        # 30 din — calendar month nahi
    "mo": 2_592_000,
    "month": 2_592_000,
    "y": 31_536_000,       # 365 din
    "year": 31_536_000,
}

_TOKEN = re.compile(r"^(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>[a-z]*)$")

MAX_WINDOWS = 10


class WindowError(ValueError):
    """Window string samajh nahi aayi."""


@dataclass(frozen=True)
class Window:
    label: str          # jaisa user ne likha — response me wahi key banti hai
    seconds: int

    def contains(self, age_seconds: int) -> bool:
        # Future ke timestamps (clock skew) ko andar hi maana jaata hai
        return age_seconds <= self.seconds


def parse_one(raw: str) -> Window:
    text = (raw or "").strip().lower()
    if not text:
        raise WindowError("khali window")

    m = _TOKEN.match(text)
    if not m:
        raise WindowError(
            f"'{raw}' samajh nahi aaya. Jaise likhiye: 1d, 3d, 7d, 1w, 1m, 24h")

    unit = m.group("unit") or "d"          # unit na ho to din maano
    if unit not in _UNITS:
        raise WindowError(
            f"'{raw}' me unit '{unit}' pata nahi. Valid: "
            f"s, min, h, d, w, m/mo (month), y")

    seconds = float(m.group("n")) * _UNITS[unit]
    if seconds <= 0:
        raise WindowError(f"'{raw}' zero ya negative hai")
    return Window(label=raw.strip(), seconds=int(seconds))


def parse(raw: str | None) -> list[Window]:
    """Comma separated windows -> list. None/khali -> khali list."""
    if not raw or not raw.strip():
        return []
    parts = [p for p in (piece.strip() for piece in raw.split(",")) if p]
    if len(parts) > MAX_WINDOWS:
        raise WindowError(f"ek baar me {MAX_WINDOWS} se zyada windows nahi")

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
    """Har window ka true/false, aur saath me hisaab ka poora byora."""
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
