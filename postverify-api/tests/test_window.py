"""Window parsing: strings like "1d, 3d, 7d, 1m"."""
from datetime import datetime, timedelta, timezone

import pytest

from app.window import MAX_WINDOWS, WindowError, evaluate, parse, parse_one


@pytest.mark.parametrize("text,seconds", [
    ("1d", 86_400),
    ("3d", 3 * 86_400),
    ("7d", 7 * 86_400),
    ("1w", 604_800),
    ("24h", 86_400),
    ("36h", 36 * 3600),
    ("1m", 30 * 86_400),
    ("1mo", 30 * 86_400),
    ("1month", 30 * 86_400),
    ("1y", 365 * 86_400),
    ("90min", 90 * 60),
    ("30s", 30),
])
def test_units(text, seconds):
    assert parse_one(text).seconds == seconds


def test_m_means_month_not_minute():
    """Many parsers read m as minute. Here it deliberately means month,
    because the requirement was "1 month". Minutes are written as min."""
    assert parse_one("1m").seconds == 30 * 86_400
    assert parse_one("1min").seconds == 60


def test_week_and_days_agree():
    assert parse_one("1w").seconds == parse_one("7d").seconds


def test_bare_number_means_days():
    assert parse_one("7").seconds == parse_one("7d").seconds


def test_case_and_spaces_ignored():
    assert parse_one("  7D  ").seconds == 7 * 86_400


def test_decimals_allowed():
    assert parse_one("1.5d").seconds == int(1.5 * 86_400)


@pytest.mark.parametrize("bad", ["", "   ", "abc", "d", "-1d", "0d", "7x", "1 2 d"])
def test_junk_rejected(bad):
    with pytest.raises(WindowError):
        parse_one(bad)


def test_unknown_unit_lists_the_valid_ones():
    with pytest.raises(WindowError, match="Valid"):
        parse_one("5foo")


# ---------------- lists ----------------

def test_parse_list():
    got = parse("1d,3d,7d,1m")
    assert [w.label for w in got] == ["1d", "3d", "7d", "1m"]


def test_empty_means_no_windows():
    assert parse(None) == [] and parse("") == [] and parse("   ") == []


def test_duplicates_dropped():
    assert [w.label for w in parse("7d,7d,7d")] == ["7d"]


def test_too_many_refused():
    many = ",".join(f"{i}d" for i in range(1, MAX_WINDOWS + 3))
    with pytest.raises(WindowError, match="No more than"):
        parse(many)


def test_one_bad_entry_fails_the_whole_thing():
    """Refusing outright is better than answering half the question."""
    with pytest.raises(WindowError):
        parse("1d,nonsense,7d")


# ---------------- evaluate ----------------

def _ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


def test_fresh_post_is_within_everything():
    got = evaluate(parse("1d,7d,1m"), _ago(hours=2))
    assert got["results"] == {"1d": True, "7d": True, "1m": True}


def test_old_post_is_within_nothing():
    got = evaluate(parse("1d,7d,1m"), _ago(days=400))
    assert got["results"] == {"1d": False, "7d": False, "1m": False}


def test_the_interesting_middle():
    """Five days old: outside 1d, inside 7d, inside 1m."""
    got = evaluate(parse("1d,3d,7d,1m"), _ago(days=5))
    assert got["results"] == {"1d": False, "3d": False, "7d": True, "1m": True}


def test_boundary_is_inclusive():
    got = evaluate(parse("1d"), _ago(seconds=86_400))
    assert got["results"]["1d"] is True


def test_just_past_boundary():
    got = evaluate(parse("1d"), _ago(seconds=86_401))
    assert got["results"]["1d"] is False


def test_future_timestamp_counts_as_within():
    """Clock skew can put a timestamp slightly in the future; that still counts."""
    got = evaluate(parse("1d"), datetime.now(timezone.utc) + timedelta(minutes=5))
    assert got["results"]["1d"] is True


def test_detail_has_cutoff_and_seconds():
    got = evaluate(parse("7d"), _ago(days=1))
    detail = got["windows"]["7d"]
    assert detail["seconds"] == 7 * 86_400
    assert detail["within"] is True
    assert detail["cutoff"].endswith("Z")
    assert got["age_seconds"] > 0
    assert got["checked_at"].endswith("Z")
