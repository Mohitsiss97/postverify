"""The HTTP contract, from creating a campaign to showing the participant
the outcome."""
from __future__ import annotations

import pytest

from app.enums import SubmissionStatus
from tests.conftest import PNG_1PX, USER, engine_result, enroll, make_campaign, submit

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
    """Adding the UI must leave the API exactly as it was."""
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    assert "/v1/submissions" in paths and "/v1/campaigns" in paths
    assert "/" not in paths, "the UI route must not appear in the API schema"


# ---------------- campaigns ----------------

def test_campaign_starts_as_draft(client):
    d = client.post("/v1/campaigns", json={"title": "Diwali Campaign"}).json()
    assert d["status"] == "draft"
    assert d["window_hours"] == 24
    assert d["assets"] == []


def test_cannot_activate_a_campaign_with_no_creatives(client):
    """Activating without an image means every submission would be rejected."""
    c = client.post("/v1/campaigns", json={"title": "Empty Campaign"}).json()
    r = client.patch(f"/v1/campaigns/{c['id']}", json={"status": "active"})
    assert r.status_code == 409
    assert r.json()["error"] == "no_assets"


def test_upload_and_download_a_creative(client):
    c = client.post("/v1/campaigns", json={"title": "Diwali Campaign"}).json()
    asset = client.post(f"/v1/campaigns/{c['id']}/assets",
                        files={"file": ("creative.png", PNG_1PX, "image/png")}).json()
    assert asset["size_bytes"] == len(PNG_1PX)
    assert len(asset["sha256"]) == 64

    got = client.get(f"/v1/campaigns/{c['id']}/assets/{asset['id']}/file")
    assert got.status_code == 200
    assert got.content == PNG_1PX, "the participant must get exactly those bytes"


def test_same_creative_twice_is_refused(client):
    c = client.post("/v1/campaigns", json={"title": "Diwali Campaign"}).json()
    files = {"file": ("creative.png", PNG_1PX, "image/png")}
    assert client.post(f"/v1/campaigns/{c['id']}/assets", files=files).status_code == 201
    again = client.post(f"/v1/campaigns/{c['id']}/assets",
                        files={"file": ("copy.png", PNG_1PX, "image/png")})
    assert again.status_code == 409
    assert again.json()["error"] == "duplicate_asset"


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
    r = client.post(f"/v1/campaigns/{campaign['id']}/enroll")     # no header
    assert r.status_code == 401
    assert r.json()["error"] == "no_user"


def test_cannot_enroll_in_a_draft_campaign(client):
    campaign = make_campaign(client, activate=False)
    r = client.post(f"/v1/campaigns/{campaign['id']}/enroll",
                    headers={"X-User-Id": USER})
    assert r.status_code == 409


def test_enrolling_twice_returns_the_same_enrollment(client):
    """Clicking twice is not a mistake."""
    campaign = make_campaign(client)
    first = enroll(client, campaign["id"])
    second = enroll(client, campaign["id"])
    assert first["id"] == second["id"]


# ---------------- submit ----------------

def test_submission_is_accepted_and_queued(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    r = submit(client, e["id"])

    assert r.status_code == 202, "202 immediately; verification runs behind it"
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
    assert r.status_code == 404, "another user's enrolment must not even be revealed"


def test_only_one_submission_in_flight(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    assert submit(client, e["id"]).status_code == 202
    second = submit(client, e["id"])
    assert second.status_code == 409
    assert second.json()["error"] == "already_pending"


def test_asset_from_another_campaign_is_refused(client):
    one = make_campaign(client, title="Campaign One")
    two = make_campaign(client, title="Campaign Two")
    e = enroll(client, one["id"])
    r = submit(client, e["id"], asset_id=two["assets"][0]["id"])
    assert r.status_code == 404


# ---------------- reading the status ----------------

async def test_user_sees_the_result_with_reasons(client, session, fake_engine):
    from sqlalchemy import select

    from app.models import Submission
    from app.processing import claim, process

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
    assert "within 24 hours" in d["message"]
    assert d["within_window"] is False
    assert len(d["records"]) == 1, "the audit trail must be visible too"


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
    from sqlalchemy import select

    from app.models import Submission
    from app.processing import claim, process

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
                    json={"approve": True,
                          "note": "participant sent a screenshot; it is correct"}
                    ).json()
    assert d["status"] == "approved"
    assert "it is correct" in d["message"]

    full = client.get(f"/v1/admin/submissions/{created['id']}").json()
    assert len(full["records"]) == 2, "a manual decision must be recorded too"
    assert "manual" in full["records"][-1]["error_detail"]


def test_cannot_decide_while_still_running(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()
    r = client.post(f"/v1/admin/submissions/{created['id']}/decide",
                    json={"approve": True, "note": "it is still running"})
    assert r.status_code == 409


def test_admin_stats(client):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])
    d = client.get("/v1/admin/stats", params={"campaign_id": campaign["id"]}).json()
    assert d["by_status"]["pending"] == 1


def test_admin_token_locks_admin_endpoints(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_token", "secret-word")

    assert client.post("/v1/campaigns", json={"title": "New Campaign"}
                       ).status_code == 401
    ok = client.post("/v1/campaigns", json={"title": "New Campaign"},
                     headers={"X-Admin-Token": "secret-word"})
    assert ok.status_code == 201


def test_user_endpoints_stay_open_when_admin_is_locked(client, monkeypatch):
    """Locking the admin surface must not stop participants working."""
    campaign = make_campaign(client)
    from app.config import settings
    monkeypatch.setattr(settings, "admin_token", "secret-word")
    r = client.post(f"/v1/campaigns/{campaign['id']}/enroll",
                    headers={"X-User-Id": USER})
    assert r.status_code == 201


# ---------------- one error shape ----------------

def test_all_errors_have_the_same_shape(client):
    """A client should write one parser: {"error", "message"}.

    The three cases below deliberately take three different routes out: our own
    HTTPException, a Pydantic validation error, and FastAPI's own 404.
    """
    cases = [
        # ours
        client.post("/v1/campaigns/9999/enroll", headers={"X-User-Id": "ravi"}),
        # Pydantic validation
        client.post("/v1/campaigns", json={"title": "x"}),
        # the framework's own 404
        client.get("/v1/no-such-route"),
    ]
    for r in cases:
        body = r.json()
        assert r.status_code >= 400
        assert "detail" not in body, f"{r.url} is still wrapped in detail"
        assert isinstance(body.get("error"), str) and body["error"]
        assert isinstance(body.get("message"), str) and body["message"]


def test_framework_404_gets_a_readable_code(client):
    r = client.get("/v1/anything")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_our_own_codes_survive(client):
    """Our own error codes must not change; only the wrapper was removed."""
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])
    r = submit(client, e["id"])
    assert r.status_code == 409
    assert r.json()["error"] == "already_pending"
    assert r.json()["submission_id"], "extra fields must survive too"


def test_validation_error_still_lists_fields(client):
    r = client.post("/v1/campaigns", json={"title": "x", "window_hours": 0})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_request"
    assert isinstance(body["fields"], list) and body["fields"]
