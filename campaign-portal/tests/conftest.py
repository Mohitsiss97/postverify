"""Test setup.

Do baatein zaroori hain:
  * env yahan, sabse upar set hota hai — app.config ek hi baar padhta hai
  * koi test asli engine ko call nahi karta; sab FakeEngine se chalte hain,
    warna tests network aur 15-second renders pe latak jaate
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

_TMP = tempfile.mkdtemp(prefix="portal-tests-")
os.environ.update(
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
    WORKER_ENABLED="false",          # worker tests khud chalate hain
    STORAGE_DIR=_TMP,
    ENGINE_URL="http://engine.invalid",
    ADMIN_TOKEN="",                  # admin endpoints khule (dev jaisa)
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
    """Asli engine ki jagah — jo bhi chahiye wahi lautata hai.

    `calls` me har call jaati hai, taaki test ye bhi jaanch sake ki kitni baar
    call hui (har call asal me ek 15-second render hoti hai — count maayne
    rakhta hai).
    """

    def __init__(self):
        self.calls: list[tuple[str, bytes]] = []
        self.results: list[EngineResult | EngineError] = []
        self.default: EngineResult | EngineError | None = None

    def queue(self, *items: EngineResult | EngineError) -> "FakeEngine":
        self.results.extend(items)
        return self

    def always(self, item: EngineResult | EngineError) -> "FakeEngine":
        self.default = item
        return self

    async def verify(self, post_url: str, image: bytes, *, filename: str = "a.jpg"):
        self.calls.append((post_url, image))
        item = self.results.pop(0) if self.results else self.default
        if item is None:
            raise AssertionError("FakeEngine ke paas lautane ko kuch nahi hai")
        if isinstance(item, EngineError):
            raise item
        return item

    async def health(self) -> dict:
        return {"status": "ok", "platforms": ["x", "instagram"]}

    async def aclose(self) -> None:
        return None


#: `published_at=None` ka matlab "engine ko time mila hi nahi" hona chahiye,
#: isliye "diya hi nahi" ke liye alag sentinel chahiye.
_DEFAULT = object()


def engine_result(*, platform="instagram", post_id="ABC123",
                  age_hours: float = 2, present=True, verdict="identical",
                  score=100, published_at=_DEFAULT) -> EngineResult:
    """Ek typical engine jawab — jitna chahiye utna badal lijiye."""
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
    return EngineError(RejectReason.ENGINE_UNAVAILABLE, "engine so raha hai")


# ---------------- fixtures ----------------

@pytest.fixture(autouse=True)
async def fresh_db():
    """Har test ko saaf DB — purane test ka data agle me na aaye."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture()
def client():
    # lifespan yahan nahi chalate — worker off rakhna hai aur tables fixture
    # pehle hi bana chuka hai.
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
