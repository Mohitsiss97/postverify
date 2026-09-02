"""Test setup.

Three things matter here:
  * the environment is set at the very top, because app.config reads it once
  * no test calls the real engine; they all run against FakeEngine, otherwise
    the suite would depend on the network and on 15-second renders
  * rate limiting is off for the suite as a whole. The middleware keeps its
    counters on the application object, which is module-level, so the counts
    would carry across tests and the suite would start failing once it grew past
    the per-minute limit — a failure that would depend on test count rather than
    on behaviour. The rate limiter has its own tests, which enable it explicitly.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

_TMP = tempfile.mkdtemp(prefix="portal-tests-")
os.environ.update(
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
    WORKER_ENABLED="false",          # the worker tests drive the loop themselves
    STORAGE_DIR=_TMP,
    ENGINE_URL="http://engine.invalid",
    ADMIN_TOKEN="",                  # admin endpoints open, as in development
    RATE_LIMIT_PER_MINUTE="0",
    LOG_LEVEL="WARNING",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.engine_client import EngineError, EngineResult  # noqa: E402
from app.enums import RejectReason  # noqa: E402
from app.main import app  # noqa: E402

USER = "user-1"
ADMIN_HEADERS: dict[str, str] = {}


# ---------------- fake engine ----------------

class FakeEngine:
    """Stands in for the real engine and returns whatever a test needs.

    Every call is appended to `calls`, so a test can also assert how many were
    made. Each call is a 15-second render in reality, so the count matters.
    """

    def __init__(self):
        self.calls: list[tuple[str, bytes]] = []
        self.results: list[EngineResult | EngineError] = []
        self.default: EngineResult | EngineError | None = None

    def queue(self, *items: EngineResult | EngineError) -> FakeEngine:
        self.results.extend(items)
        return self

    def always(self, item: EngineResult | EngineError) -> FakeEngine:
        self.default = item
        return self

    async def verify(self, post_url: str, image: bytes, *, filename: str = "a.jpg",
                     request_id: str | None = None):
        self.calls.append((post_url, image))
        item = self.results.pop(0) if self.results else self.default
        if item is None:
            raise AssertionError("FakeEngine has nothing left to return")
        if isinstance(item, EngineError):
            raise item
        return item

    async def health(self) -> dict:
        return {"status": "ok", "platforms": ["x", "instagram"]}

    async def aclose(self) -> None:
        return None


#: `published_at=None` has to mean "the engine found no time at all", so
#: "the caller did not specify one" needs a separate sentinel.
_DEFAULT = object()


def engine_result(*, platform="instagram", post_id="ABC123",
                  age_hours: float = 2, present=True, verdict="identical",
                  score=100, published_at=_DEFAULT) -> EngineResult:
    """A typical engine response; override whatever the test cares about."""
    if published_at is _DEFAULT:
        published_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return EngineResult(
        platform=platform,
        post_id=post_id,
        canonical_url=f"https://example.test/{platform}/{post_id}",
        published_at=published_at,
        age_seconds=int((datetime.now(timezone.utc) - published_at).total_seconds())
        if published_at else None,
        time_method="headless-page",
        image_present=present,
        image_verdict=verdict,
        image_score=score,
        images_checked=1,
        matched_tier="post" if present else None,
        raw={"platform": platform, "post_id": post_id},
    )


def engine_down() -> EngineError:
    return EngineError(RejectReason.ENGINE_UNAVAILABLE, "the engine is asleep")


# ---------------- fixtures ----------------

@pytest.fixture(autouse=True)
async def fresh_db():
    """A clean database per test, so no test inherits another's data."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture()
def client():
    # The lifespan is not run here: the worker must stay off, and the fixture
    # above has already created the tables.
    return TestClient(app)


@pytest.fixture()
def fake_engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture()
async def session():
    async with SessionLocal() as s:
        yield s


# ---------------- helpers ----------------

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "0557bfabd40000000049454e44ae426082")


def make_campaign(client, *, title="Diwali Campaign", window_hours=24,
                  platforms=None, with_asset=True, activate=True) -> dict:
    body = {"title": title, "window_hours": window_hours}
    if platforms:
        body["allowed_platforms"] = platforms
    campaign = client.post("/v1/campaigns", json=body).json()

    if with_asset:
        client.post(f"/v1/campaigns/{campaign['id']}/assets",
                    files={"file": ("creative.png", PNG_1PX, "image/png")})
    if activate:
        client.patch(f"/v1/campaigns/{campaign['id']}", json={"status": "active"})
    return client.get(f"/v1/campaigns/{campaign['id']}").json()


def enroll(client, campaign_id: int, user: str = USER) -> dict:
    return client.post(f"/v1/campaigns/{campaign_id}/enroll",
                       headers={"X-User-Id": user}).json()


def submit(client, enrollment_id: int, *, url="https://www.instagram.com/p/ABC123/",
           platform="instagram", asset_id=None, user: str = USER):
    body = {"enrollment_id": enrollment_id, "post_url": url, "platform": platform}
    if asset_id is not None:
        body["asset_id"] = asset_id
    return client.post("/v1/submissions", json=body, headers={"X-User-Id": user})
