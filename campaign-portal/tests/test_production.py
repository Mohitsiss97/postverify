"""The things that only matter once this is deployed.

Startup validation, request correlation, security headers and rate limiting.
Each of these is easy to break silently during a refactor and expensive to
discover in production, which is why they are pinned here.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.main import app
from tests.conftest import USER, make_campaign


def _production(**overrides) -> Settings:
    """A settings object that would pass in production, minus what a test breaks."""
    base = {
        "env": "production",
        "admin_token": "a-real-token",
        "database_url": "postgresql+asyncpg://portal:pw@db/portal",
        "engine_url": "https://engine.internal",
        "cors_origins": "",
        "rate_limit_per_minute": 120,
    }
    return Settings(**{**base, **overrides})


# ---------------- startup validation ----------------

def test_a_correct_production_config_starts_cleanly():
    assert _production().validate_for_start() == []


def test_production_refuses_to_start_without_an_admin_token():
    """Open admin endpoints in production means anyone can approve themselves."""
    with pytest.raises(ConfigError, match="ADMIN_TOKEN"):
        _production(admin_token=None).validate_for_start()


def test_production_refuses_sqlite():
    """SQLite serialises writes, so a second worker fails on lock contention."""
    with pytest.raises(ConfigError, match="SQLite"):
        _production(database_url="sqlite+aiosqlite:///./portal.db").validate_for_start()


def test_production_refuses_a_wildcard_cors_origin():
    with pytest.raises(ConfigError, match="CORS_ORIGINS"):
        _production(cors_origins="*").validate_for_start()


def test_plaintext_engine_over_the_network_is_a_warning_not_a_refusal():
    """It is a real risk, but it is legitimate inside a private network, so the
    operator is told rather than blocked."""
    warnings = _production(engine_url="http://engine.example.com").validate_for_start()
    assert any("http://" in w for w in warnings)


def test_a_local_engine_over_plain_http_is_fine():
    assert _production(engine_url="http://localhost:8200").validate_for_start() == []


def test_development_only_warns_about_the_open_admin_surface():
    warnings = Settings(env="development", admin_token=None).validate_for_start()
    assert any("ADMIN_TOKEN" in w for w in warnings)


def test_describe_never_leaks_a_secret():
    """This is logged at startup, so a secret in it would be a secret in the logs."""
    described = _production(engine_token="engine-secret").describe()
    flat = repr(described)
    assert "a-real-token" not in flat
    assert "engine-secret" not in flat
    assert described["admin_token_set"] is True
    assert described["engine_token_set"] is True


# ---------------- request correlation ----------------

def test_every_response_carries_a_request_id(client):
    assert client.get("/health").headers["X-Request-ID"]


def test_an_incoming_request_id_is_kept(client):
    """A trace started upstream must continue here rather than restart."""
    r = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"


# ---------------- security headers ----------------

def test_security_headers_are_present(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_the_ui_is_served_under_the_same_policy(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Content-Security-Policy" in r.headers


# ---------------- rate limiting ----------------
#
# The middleware keeps its counters on the application object, which is shared
# across the suite. Each test below therefore identifies itself with its own
# X-User-Id, which is also the rate-limit key, so no test inherits another's
# count. Anything relying on the shared client-IP bucket would pass alone and
# fail in the suite.

def _limit(monkeypatch, per_minute: int) -> TestClient:
    from app.config import settings
    monkeypatch.setattr(settings, "rate_limit_per_minute", per_minute)
    return TestClient(app)


def test_writes_are_rate_limited(monkeypatch):
    me = {"X-User-Id": "rl-writes"}
    fresh = _limit(monkeypatch, 3)

    codes = [fresh.post("/v1/campaigns", json={"title": f"Campaign {i}"},
                        headers=me).status_code for i in range(8)]
    assert 429 in codes

    limited = fresh.post("/v1/campaigns", json={"title": "One more"}, headers=me)
    assert limited.json()["error"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1


def test_reads_are_never_rate_limited(monkeypatch):
    """The UI polls for a submission's result; throttling that would work
    against the participant at exactly the wrong moment."""
    me = {"X-User-Id": "rl-reads"}
    fresh = _limit(monkeypatch, 1)

    for _ in range(5):
        fresh.post("/v1/campaigns", json={"title": "Flood"}, headers=me)
    for _ in range(10):
        assert fresh.get("/v1/campaigns", headers=me).status_code == 200
    assert fresh.get("/health", headers=me).status_code == 200


def test_one_participant_cannot_exhaust_another_s_allowance(client, monkeypatch):
    """Several people behind one office NAT share an IP, so the limit keys on
    the participant whenever their identity is known."""
    # Build the campaign before lowering the limit, so the setup itself is not
    # what gets throttled.
    campaign = make_campaign(client)
    fresh = _limit(monkeypatch, 2)

    for _ in range(6):
        fresh.post(f"/v1/campaigns/{campaign['id']}/enroll",
                   headers={"X-User-Id": "noisy-user"})

    quiet = fresh.post(f"/v1/campaigns/{campaign['id']}/enroll",
                       headers={"X-User-Id": "quiet-user"})
    assert quiet.status_code == 201, quiet.json()


# ---------------- unhandled errors ----------------

def test_an_unexpected_crash_does_not_leak_internals(client, monkeypatch):
    """Exception text routinely carries paths, SQL and sometimes credentials.
    The caller gets a request ID to quote instead."""
    from app.routers import campaigns as campaigns_router

    async def boom(*a, **k):
        raise RuntimeError("connection to postgres://portal:hunter2@db failed")

    monkeypatch.setattr(campaigns_router, "_get", boom)
    # The handler under test is the one that turns a crash into a response, so
    # the client must not re-raise the exception before it runs.
    quiet = TestClient(app, raise_server_exceptions=False)
    r = quiet.get("/v1/campaigns/1", headers={"X-User-Id": USER})

    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "server_error"
    assert "hunter2" not in repr(body)
    assert body["request_id"]
