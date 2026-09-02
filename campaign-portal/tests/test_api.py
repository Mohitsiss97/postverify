"""HTTP contract — campaign banane se lekar user ko nateeja dikhne tak."""
from __future__ import annotations

import pytest

from app.enums import SubmissionStatus
from tests.conftest import (PNG_1PX, USER, enroll, engine_result, make_campaign,
                            submit)


# ---------------- meta ----------------

def test_health(client):
    d = client.get("/health").json()
    assert d["status"] == "ok" and d["window_hours"] == 24


def test_root_serves_the_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Campaign Portal" in r.text
    assert "text/html" in r.headers["content-type"]


def test_ui_does_not_touch_the_api(client):
    """UI add hone se API waise ki waisi rehni chahiye."""
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    assert "/v1/submissions" in paths and "/v1/campaigns" in paths
    assert "/" not in paths, "UI route API schema me nahi aana chahiye"


# ---------------- campaigns ----------------

def test_campaign_starts_as_draft(client):
    d = client.post("/v1/campaigns", json={"title": "Diwali Campaign"}).json()
    assert d["status"] == "draft"
    assert d["window_hours"] == 24
    assert d["assets"] == []


def test_cannot_activate_a_campaign_with_no_creatives(client):
    """Bina image ke activate karna matlab har submission reject hoga."""
    c = client.post("/v1/campaigns", json={"title": "Khaali Campaign"}).json()
    r = client.patch(f"/v1/campaigns/{c['id']}", json={"status": "active"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "no_assets"


def test_upload_and_download_a_creative(client):
    c = client.post("/v1/campaigns", json={"title": "Diwali Campaign"}).json()
    asset = client.post(f"/v1/campaigns/{c['id']}/assets",
                        files={"file": ("creative.png", PNG_1PX, "image/png")}).json()
    assert asset["size_bytes"] == len(PNG_1PX)
    assert len(asset["sha256"]) == 64

    got = client.get(f"/v1/campaigns/{c['id']}/assets/{asset['id']}/file")
    assert got.status_code == 200
    assert got.content == PNG_1PX, "user ko bilkul wahi bytes milni chahiye"


def test_same_creative_twice_is_refused(client):
    c = client.post("/v1/campaigns", json={"title": "Diwali Campaign"}).json()
    files = {"file": ("creative.png", PNG_1PX, "image/png")}
    assert client.post(f"/v1/campaigns/{c['id']}/assets", files=files).status_code == 201
    again = client.post(f"/v1/campaigns/{c['id']}/assets",
                        files={"file": ("copy.png", PNG_1PX, "image/png")})
    assert again.status_code == 409
    assert again.json()["detail"]["error"] == "duplicate_asset"


def test_only_images_allowed(client):
    c = client.post("/v1/campaigns", json={"title": "Diwali Campaign"}).json()
    r = client.post(f"/v1/campaigns/{c['id']}/assets",
                    files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_unknown_platform_is_refused(client):
    r = client.post("/v1/campaigns",
                    json={"title": "Diwali Campaign", "allowed_platforms": ["orkut"]})
    assert r.status_code == 422
    assert "orkut" in r.json()["message"]


# ---------------- enroll ----------------

def test_enroll_needs_a_user(client):
    campaign = make_campaign(client)
    r = client.post(f"/v1/campaigns/{campaign['id']}/enroll")     # bina header
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "no_user"


def test_cannot_enroll_in_a_draft_campaign(client):
    campaign = make_campaign(client, activate=False)
    r = client.post(f"/v1/campaigns/{campaign['id']}/enroll",
                    headers={"X-User-Id": USER})
    assert r.status_code == 409


def test_enrolling_twice_returns_the_same_enrollment(client):
    """Dobara click karna galti nahi hai."""
    campaign = make_campaign(client)
    first = enroll(client, campaign["id"])
    second = enroll(client, campaign["id"])
    assert first["id"] == second["id"]


# ---------------- submit ----------------

def test_submission_is_accepted_and_queued(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    r = submit(client, e["id"])

    assert r.status_code == 202, "turant 202 — verify peeche chalega"
    d = r.json()
    assert d["status"] == SubmissionStatus.PENDING
    assert d["verified_at"] is None


@pytest.mark.parametrize("url", ["ftp://x.com/a", "instagram.com/p/A", "  "])
def test_bad_url_is_refused_upfront(client, url):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    assert submit(client, e["id"], url=url).status_code == 422


def test_unknown_platform_on_submit(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    r = submit(client, e["id"], platform="orkut")
    assert r.status_code == 422
    assert "orkut" in r.json()["message"]


def test_cannot_submit_to_someone_elses_enrollment(client):
    campaign = make_campaign(client)
    mine = enroll(client, campaign["id"], user="user-1")
    r = submit(client, mine["id"], user="user-2")
    assert r.status_code == 404, "doosre ki enrollment ka pata bhi nahi chalna chahiye"


def test_only_one_submission_in_flight(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    assert submit(client, e["id"]).status_code == 202
    second = submit(client, e["id"])
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "already_pending"


def test_asset_from_another_campaign_is_refused(client):
    one = make_campaign(client, title="Campaign Ek")
    two = make_campaign(client, title="Campaign Do")
    e = enroll(client, one["id"])
    r = submit(client, e["id"], asset_id=two["assets"][0]["id"])
    assert r.status_code == 404


# ---------------- status dekhna ----------------

async def test_user_sees_the_result_with_reasons(client, session, fake_engine):
    from app.processing import claim, process
    from app.models import Submission
    from sqlalchemy import select

    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()

    fake_engine.always(engine_result(age_hours=40))
    submission = await session.scalar(
        select(Submission).where(Submission.id == created["id"]))
    await claim(session, submission.id)
    await session.refresh(submission)
    await process(session, submission, fake_engine)

    d = client.get(f"/v1/submissions/{created['id']}",
                   headers={"X-User-Id": USER}).json()
    assert d["status"] == "rejected"
    assert d["reason"] == "too_old"
    assert "24 ghante" in d["message"]
    assert d["within_window"] is False
    assert len(d["records"]) == 1, "audit trail bhi dikhni chahiye"


def test_cannot_read_someone_elses_submission(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"], user="user-1")
    created = submit(client, e["id"], user="user-1").json()
    r = client.get(f"/v1/submissions/{created['id']}",
                   headers={"X-User-Id": "user-2"})
    assert r.status_code == 404


def test_my_submissions_only_shows_mine(client):
    campaign = make_campaign(client)
    mine = enroll(client, campaign["id"], user="user-1")
    theirs = enroll(client, campaign["id"], user="user-2")
    submit(client, mine["id"], user="user-1")
    submit(client, theirs["id"], user="user-2")

    rows = client.get("/v1/submissions", headers={"X-User-Id": "user-1"}).json()
    assert len(rows) == 1
    assert rows[0]["enrollment_id"] == mine["id"]


# ---------------- admin ----------------

async def test_admin_can_override_a_rejection(client, session, fake_engine):
    from app.processing import claim, process
    from app.models import Submission
    from sqlalchemy import select

    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()

    fake_engine.always(engine_result(present=False))
    submission = await session.scalar(
        select(Submission).where(Submission.id == created["id"]))
    await claim(session, submission.id)
    await session.refresh(submission)
    await process(session, submission, fake_engine)

    d = client.post(f"/v1/admin/submissions/{created['id']}/decide",
                    json={"approve": True, "note": "user ne screenshot bheja, sahi hai"}
                    ).json()
    assert d["status"] == "approved"
    assert "sahi hai" in d["message"]

    full = client.get(f"/v1/admin/submissions/{created['id']}").json()
    assert len(full["records"]) == 2, "manual faisla bhi record me jana chahiye"
    assert "manual" in full["records"][-1]["error_detail"]


def test_cannot_decide_while_still_running(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()
    r = client.post(f"/v1/admin/submissions/{created['id']}/decide",
                    json={"approve": True, "note": "abhi to chal raha hai"})
    assert r.status_code == 409


def test_admin_stats(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])
    d = client.get("/v1/admin/stats", params={"campaign_id": campaign["id"]}).json()
    assert d["by_status"]["pending"] == 1


def test_admin_token_locks_admin_endpoints(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_token", "gupt-shabd")

    assert client.post("/v1/campaigns", json={"title": "Nayi Campaign"}
                       ).status_code == 401
    ok = client.post("/v1/campaigns", json={"title": "Nayi Campaign"},
                     headers={"X-Admin-Token": "gupt-shabd"})
    assert ok.status_code == 201


def test_user_endpoints_stay_open_when_admin_is_locked(client, monkeypatch):
    """Admin token lagne se users ka kaam nahi rukna chahiye."""
    campaign = make_campaign(client)
    from app.config import settings
    monkeypatch.setattr(settings, "admin_token", "gupt-shabd")
    r = client.post(f"/v1/campaigns/{campaign['id']}/enroll",
                    headers={"X-User-Id": USER})
    assert r.status_code == 201
