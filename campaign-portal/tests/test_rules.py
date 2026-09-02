"""Verification rules — poore portal ka dil yahi hai.

Har test ek business rule check karta hai. Engine fake hai, to ye tez chalte
hain aur network pe depend nahi karte.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.enums import RejectReason, SubmissionStatus
from app.models import Submission
from app.processing import claim, process
from app.verification import verify_submission
from tests.conftest import (USER, FakeEngine, enroll, engine_down, engine_result,
                            make_campaign, submit)


async def _pending(session) -> Submission:
    return await session.scalar(
        select(Submission).where(Submission.status == SubmissionStatus.PENDING))


async def run(session, fake_engine) -> Submission:
    """Ek pending submission ko poore rules se guzaro."""
    submission = await _pending(session)
    assert submission is not None, "koi pending submission hi nahi"
    await claim(session, submission.id)
    await session.refresh(submission)
    return await process(session, submission, fake_engine)


# ---------------- sab sahi ----------------

async def test_everything_correct_is_approved(client, session, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"], asset_id=campaign["assets"][0]["id"])

    fake_engine.always(engine_result(age_hours=2))
    submission = await run(session, fake_engine)

    assert submission.status == SubmissionStatus.APPROVED
    assert submission.reason is None
    assert submission.within_window is True
    assert submission.image_score == 100
    assert submission.matched_asset_id == campaign["assets"][0]["id"]
    assert "Sab sahi" in submission.message


async def test_approval_completes_the_enrollment(client, session, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])
    fake_engine.always(engine_result())

    await run(session, fake_engine)

    from app.models import Enrollment
    enrollment = await session.get(Enrollment, e["id"])
    assert enrollment.status == "completed"
    assert enrollment.completed_at is not None


# ---------------- 24 ghante ka window ----------------

async def test_post_older_than_window_is_rejected(client, session, fake_engine):
    campaign = make_campaign(client, window_hours=24)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])

    fake_engine.always(engine_result(age_hours=30))
    submission = await run(session, fake_engine)

    assert submission.status == SubmissionStatus.REJECTED
    assert submission.reason == RejectReason.TOO_OLD
    assert submission.within_window is False
    assert "24 ghante" in submission.message


async def test_just_inside_the_window_passes(client, session, fake_engine):
    campaign = make_campaign(client, window_hours=24)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])

    fake_engine.always(engine_result(age_hours=23.9))
    submission = await run(session, fake_engine)
    assert submission.status == SubmissionStatus.APPROVED


async def test_campaign_can_set_its_own_window(client, session, fake_engine):
    """Campaign ka window default se upar hai — 48 ghante purani post chalegi."""
    campaign = make_campaign(client, window_hours=72)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])

    fake_engine.always(engine_result(age_hours=48))
    submission = await run(session, fake_engine)
    assert submission.status == SubmissionStatus.APPROVED


async def test_old_post_does_not_try_other_creatives(client, session, fake_engine):
    """Time fail ho gaya to baaki creatives try karne ka koi matlab nahi —
    har try ek 15-second render hai."""
    campaign = make_campaign(client)
    client.post(f"/v1/campaigns/{campaign['id']}/assets",
                files={"file": ("second.png", b"\x89PNG\r\n\x1a\ndifferent",
                                "image/png")})
    e = enroll(client, campaign["id"])
    submit(client, e["id"])

    fake_engine.always(engine_result(age_hours=99, present=False))
    submission = await run(session, fake_engine)

    assert submission.reason == RejectReason.TOO_OLD
    assert len(fake_engine.calls) == 1, "time fail hone ke baad rukna chahiye tha"


# ---------------- image ----------------

async def test_image_mismatch_is_rejected(client, session, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"], asset_id=campaign["assets"][0]["id"])

    fake_engine.always(engine_result(present=False, verdict="different", score=4))
    submission = await run(session, fake_engine)

    assert submission.status == SubmissionStatus.REJECTED
    assert submission.reason == RejectReason.IMAGE_MISMATCH
    assert "match nahi hui" in submission.message


async def test_tries_other_creatives_when_asset_not_given(client, session,
                                                          fake_engine):
    """User ne nahi bataya kaunsa creative post kiya — hum dhoondhte hain."""
    campaign = make_campaign(client)
    client.post(f"/v1/campaigns/{campaign['id']}/assets",
                files={"file": ("second.png", b"\x89PNG\r\n\x1a\nsecond",
                                "image/png")})
    e = enroll(client, campaign["id"])
    submit(client, e["id"])          # asset_id nahi diya

    fake_engine.queue(
        engine_result(present=False, verdict="different", score=3),   # pehla nahi
        engine_result(present=True),                                  # doosra haan
    )
    submission = await run(session, fake_engine)

    assert submission.status == SubmissionStatus.APPROVED
    assert len(fake_engine.calls) == 2


async def test_asset_id_means_exactly_one_call(client, session, fake_engine):
    """User bata de to ek hi call — Instagram pe ye 15 second bachata hai."""
    campaign = make_campaign(client)
    client.post(f"/v1/campaigns/{campaign['id']}/assets",
                files={"file": ("second.png", b"\x89PNG\r\n\x1a\nsecond",
                                "image/png")})
    e = enroll(client, campaign["id"])
    submit(client, e["id"], asset_id=campaign["assets"][0]["id"])

    fake_engine.always(engine_result(present=False))
    await run(session, fake_engine)
    assert len(fake_engine.calls) == 1


# ---------------- platform ----------------

async def test_declared_platform_must_match_the_link(client, session, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"], platform="facebook")     # user ne facebook kaha

    fake_engine.always(engine_result(platform="instagram"))   # link instagram ka
    submission = await run(session, fake_engine)

    assert submission.reason == RejectReason.WRONG_PLATFORM
    assert "facebook" in submission.message and "instagram" in submission.message


async def test_campaign_can_limit_platforms(client, session, fake_engine):
    campaign = make_campaign(client, platforms=["instagram"])
    e = enroll(client, campaign["id"])
    submit(client, e["id"], platform="x", url="https://x.com/a/status/1")

    fake_engine.always(engine_result(platform="x"))
    submission = await run(session, fake_engine)

    assert submission.reason == RejectReason.WRONG_PLATFORM
    assert "allowed nahi" in submission.message


# ---------------- duplicate ----------------

async def test_same_post_cannot_be_submitted_twice(client, session, fake_engine):
    campaign = make_campaign(client)
    first = enroll(client, campaign["id"], user="user-1")
    second = enroll(client, campaign["id"], user="user-2")

    fake_engine.always(engine_result(post_id="SAME"))

    submit(client, first["id"], user="user-1")
    approved = await run(session, fake_engine)
    assert approved.status == SubmissionStatus.APPROVED

    submit(client, second["id"], user="user-2")
    duplicate = await run(session, fake_engine)
    assert duplicate.status == SubmissionStatus.REJECTED
    assert duplicate.reason == RejectReason.DUPLICATE


async def test_rejected_post_frees_the_link(client, session, fake_engine):
    """Reject hone pe dedupe key hat jaati hai — wahi post sudhaar ke dobara
    bheji ja sake."""
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])

    submit(client, e["id"])
    fake_engine.always(engine_result(age_hours=99))
    rejected = await run(session, fake_engine)
    assert rejected.dedupe_key is None


# ---------------- engine ki dikkat ----------------

async def test_engine_down_is_retried_not_rejected(client, session, fake_engine):
    """Engine ka down hona user ki galti nahi — reject nahi, dobara koshish."""
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])

    fake_engine.always(engine_down())
    submission = await run(session, fake_engine)

    assert submission.status == SubmissionStatus.PENDING
    assert submission.next_attempt_at is not None
    assert submission.attempts == 1


async def test_retries_stop_after_max_attempts(client, session, fake_engine,
                                               monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "max_attempts", 2)

    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])
    fake_engine.always(engine_down())

    await run(session, fake_engine)          # attempt 1 -> pending
    submission = await run(session, fake_engine)   # attempt 2 -> haar

    assert submission.status == SubmissionStatus.ERROR
    assert submission.attempts == 2


async def test_unsupported_url_is_a_real_rejection(client, session, fake_engine):
    """Ye user ki galti hai, takneeki dikkat nahi — isliye retry nahi hoga."""
    from app.engine_client import EngineError
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])

    fake_engine.always(EngineError(RejectReason.UNSUPPORTED_URL,
                                   "ye link kisi platform ka nahi"))
    submission = await run(session, fake_engine)
    assert submission.status == SubmissionStatus.ERROR
    assert submission.reason == RejectReason.UNSUPPORTED_URL
    assert submission.next_attempt_at is None, "isme retry nahi hona chahiye"


async def test_time_missing_is_retryable(client, session, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])

    fake_engine.always(engine_result(published_at=None))
    submission = await run(session, fake_engine)

    assert submission.reason == RejectReason.TIME_NOT_AVAILABLE
    assert submission.status == SubmissionStatus.PENDING


# ---------------- campaign ki halat ----------------

async def test_campaign_without_assets_says_so(client, session, fake_engine):
    campaign = make_campaign(client, with_asset=False, activate=False)
    client.patch(f"/v1/campaigns/{campaign['id']}", json={"status": "active"})
    # activate mana kar dega, isliye seedha DB me active kar dete hain
    from app.models import Campaign
    row = await session.get(Campaign, campaign["id"])
    row.status = "active"
    await session.commit()

    e = enroll(client, campaign["id"])
    submit(client, e["id"])
    submission = await run(session, fake_engine)

    assert submission.reason == RejectReason.NO_CAMPAIGN_ASSETS
    assert len(fake_engine.calls) == 0, "bina creative ke engine call nahi honi chahiye"


# ---------------- audit trail ----------------

async def test_every_attempt_leaves_a_record(client, session, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"], asset_id=campaign["assets"][0]["id"])

    fake_engine.always(engine_result())
    submission = await run(session, fake_engine)
    await session.refresh(submission, ["records"])

    assert len(submission.records) == 1
    record = submission.records[0]
    assert record.outcome == "approved"
    assert record.attempt == 1
    assert record.checked_asset_sha256, "kis image se compare hua, wo record me ho"
    assert record.engine_response is not None
    assert record.evidence_path, "evidence file bhi likhni chahiye"


async def test_evidence_file_is_written(client, session, fake_engine):
    import json
    from pathlib import Path

    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])
    fake_engine.always(engine_result())

    submission = await run(session, fake_engine)
    await session.refresh(submission, ["records"])

    path = Path(submission.records[0].evidence_path)
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["submission_id"] == submission.id
    assert saved["outcome"] == "approved"
    assert saved["checked_asset"]["sha256"]


async def test_record_survives_asset_change(client, session, fake_engine):
    """Admin creative badal de to bhi record batata hai kis image se check hua tha."""
    campaign = make_campaign(client)
    asset_id = campaign["assets"][0]["id"]
    e = enroll(client, campaign["id"])
    submit(client, e["id"], asset_id=asset_id)
    fake_engine.always(engine_result())

    submission = await run(session, fake_engine)
    await session.refresh(submission, ["records"])
    original_sha = submission.records[0].checked_asset_sha256

    client.delete(f"/v1/campaigns/{campaign['id']}/assets/{asset_id}")

    await session.refresh(submission, ["records"])
    assert submission.records[0].checked_asset_sha256 == original_sha


async def test_image_mismatch_still_reports_that_time_was_fine(client, session,
                                                               fake_engine):
    """User ko dikhna chahiye ki timing theek thi, sirf image galat thi."""
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"], asset_id=campaign["assets"][0]["id"])

    fake_engine.always(engine_result(age_hours=3, present=False))
    submission = await run(session, fake_engine)

    assert submission.reason == RejectReason.IMAGE_MISMATCH
    assert submission.within_window is True, "time to pass hua tha"
    assert submission.age_seconds is not None


async def test_duplicate_also_reports_the_window(client, session, fake_engine):
    campaign = make_campaign(client)
    first = enroll(client, campaign["id"], user="user-1")
    second = enroll(client, campaign["id"], user="user-2")
    fake_engine.always(engine_result(post_id="SAME", age_hours=1))

    submit(client, first["id"], user="user-1")
    await run(session, fake_engine)
    submit(client, second["id"], user="user-2")
    duplicate = await run(session, fake_engine)

    assert duplicate.reason == RejectReason.DUPLICATE
    assert duplicate.within_window is True
