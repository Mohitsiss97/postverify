"""Offline timestamp maths — X aur LinkedIn."""
from datetime import datetime, timezone

import pytest

from app.resolvers.graph import shortcode_to_media_id
from app.resolvers.snowflake import (
    X_EPOCH_MS,
    InvalidSnowflakeError,
    linkedin_created_at,
    x_created_at,
)


def test_x_known_tweet():
    """Real anchor: funding-secured tweet, 7 Aug 2018 16:48 UTC."""
    assert x_created_at(1026872652290379776) == datetime(
        2018, 8, 7, 16, 48, 13, 334000, tzinfo=timezone.utc)


def test_x_roundtrip():
    target = datetime(2023, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
    fake_id = ((int(target.timestamp() * 1000) - X_EPOCH_MS) << 22) | 4242
    assert x_created_at(fake_id) == target


def test_x_pre_snowflake_rejected():
    with pytest.raises(InvalidSnowflakeError):
        x_created_at(20)          # pehla tweet — snowflake se pehle ka


def test_x_monotonic():
    assert x_created_at(1_500_000_000_000_000_000) < x_created_at(1_600_000_000_000_000_000)


def test_linkedin_ranges():
    assert linkedin_created_at(7000000000000000000).year == 2022
    assert linkedin_created_at(7100000000000000000).year == 2023
    assert linkedin_created_at(7250000000000000000).year == 2024


def test_linkedin_roundtrip():
    target = datetime(2025, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
    assert linkedin_created_at((int(target.timestamp() * 1000) << 22) | 7) == target


@pytest.mark.parametrize("bad", [12345, 0, -1])
def test_garbage_ids_rejected(bad):
    with pytest.raises(InvalidSnowflakeError):
        linkedin_created_at(bad)


# ---------------- Instagram shortcode ----------------

def test_shortcode_decodes_to_media_id():
    assert shortcode_to_media_id("B") == 1
    assert shortcode_to_media_id("BA") == 64
    assert shortcode_to_media_id("CxYzAbC1234") > 0


def test_shortcode_rejects_junk():
    with pytest.raises(ValueError):
        shortcode_to_media_id("bad!code")
