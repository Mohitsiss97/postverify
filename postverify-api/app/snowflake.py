"""Offline timestamp extraction — the time is embedded in the ID itself.

X (Twitter) and LinkedIn both use 64-bit snowflake-style IDs whose upper bits
are a millisecond timestamp. That means no API call, no token and no rate
limit — just integer arithmetic, entirely offline.

Layout (identical shape for both):
    [ 41 bits timestamp_ms ][ 22 bits machine + sequence ]

Only the epoch differs:
    X         -> custom epoch 1288834974657 ms (2010-11-04 01:42:54.657 UTC)
    LinkedIn  -> plain Unix epoch (0)
"""
from __future__ import annotations

from datetime import datetime, timezone

# X/Twitter's custom snowflake epoch, in milliseconds.
X_EPOCH_MS = 1288834974657
TIMESTAMP_SHIFT = 22

# Sanity window: a result outside this range means the ID was not a snowflake.
_MIN_MS = 1_100_000_000_000   # ~2004
_MAX_MS = 4_100_000_000_000   # ~2099


class InvalidSnowflakeError(ValueError):
    pass


def _to_dt(ms: int, source_id: str) -> datetime:
    if not _MIN_MS <= ms <= _MAX_MS:
        raise InvalidSnowflakeError(
            f"The timestamp decoded from ID {source_id} is not within a "
            f"plausible range ({ms} ms)")
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def x_created_at(tweet_id: str | int) -> datetime:
    """X/Twitter status ID -> UTC datetime, millisecond precision."""
    i = int(tweet_id)
    if i <= 0:
        raise InvalidSnowflakeError("A tweet ID must be positive")
    # Pre-snowflake tweets (before Nov 2010) have much smaller IDs that carry
    # no timestamp at all.
    if i < 29700859247:
        raise InvalidSnowflakeError(
            "This is a pre-2010 tweet ID, from before snowflake — no timestamp "
            "is encoded in it")
    return _to_dt((i >> TIMESTAMP_SHIFT) + X_EPOCH_MS, str(i))


def linkedin_created_at(activity_id: str | int) -> datetime:
    """LinkedIn activity/ugcPost URN ID -> UTC datetime."""
    i = int(activity_id)
    if i <= 0:
        raise InvalidSnowflakeError("An activity ID must be positive")
    return _to_dt(i >> TIMESTAMP_SHIFT, str(i))
