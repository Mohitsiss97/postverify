"""Shared test setup.

Rate limiting is disabled for the suite as a whole. The middleware keeps its
counters on the application object, which is module-level, so the counts would
otherwise carry across tests and the suite would start failing once it grew past
the per-minute limit — a failure that would depend on test count rather than on
behaviour. The rate limiter has its own tests, which enable it explicitly.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
