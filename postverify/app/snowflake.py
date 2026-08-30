"""Offline timestamp extraction — ID ke andar hi time embedded hota hai.

X (Twitter) aur LinkedIn dono 64-bit snowflake-style IDs use karte hain jinke
upper bits millisecond timestamp hote hain. Matlab: koi API call nahi, koi
token nahi, koi rate limit nahi — sirf integer maths. 100% offline.

Layout (dono ke liye same shape):
    [ 41 bits timestamp_ms ][ 22 bits machine + sequence ]

Farq sirf epoch ka hai:
    X         -> custom epoch 1288834974657 ms (2010-11-04 01:42:54.657 UTC)
    LinkedIn  -> plain Unix epoch (0)
"""
from __future__ import annotations

from datetime import datetime, timezone

# X/Twitter ka custom snowflake epoch (ms)
X_EPOCH_MS = 1288834974657
TIMESTAMP_SHIFT = 22

# Sanity window — isse bahar ka result matlab ID galat hai
_MIN_MS = 1_100_000_000_000   # ~2004
_MAX_MS = 4_100_000_000_000   # ~2099


class InvalidSnowflakeError(ValueError):
    pass


def _to_dt(ms: int, source_id: str) -> datetime:
    if not _MIN_MS <= ms <= _MAX_MS:
        raise InvalidSnowflakeError(
            f"ID {source_id} se nikla timestamp plausible range me nahi hai ({ms} ms)")
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def x_created_at(tweet_id: str | int) -> datetime:
    """X/Twitter status ID -> UTC datetime (millisecond precision)."""
    i = int(tweet_id)
    if i <= 0:
        raise InvalidSnowflakeError("tweet id positive hona chahiye")
    # Pre-snowflake tweets (Nov 2010 se pehle) ke IDs chhote hain — unme time nahi hai
    if i < 29700859247:
        raise InvalidSnowflakeError(
            "Ye pre-2010 tweet ID hai (snowflake se pehle) — ID me timestamp encoded nahi hai")
    return _to_dt((i >> TIMESTAMP_SHIFT) + X_EPOCH_MS, str(i))


def linkedin_created_at(activity_id: str | int) -> datetime:
    """LinkedIn activity/ugcPost URN ID -> UTC datetime."""
    i = int(activity_id)
    if i <= 0:
        raise InvalidSnowflakeError("activity id positive hona chahiye")
    return _to_dt(i >> TIMESTAMP_SHIFT, str(i))
