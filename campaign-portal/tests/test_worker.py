"""The worker: taking work off the queue, never taking it twice, and
honouring the retry schedule."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import worker
from app.db import SessionLocal
from app.enums import SubmissionStatus
from app.models import Submission
from app.processing import claim
from tests.conftest import engine_down, engine_result, enroll, make_campaign, submit


async def test_worker_picks_up_and_finishes(client, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()

    fake_engine.always(engine_result())
    assert await worker.run_once(fake_engine) == 1

    async with SessionLocal() as session:
        submission = await session.get(Submission, created["id"])
        assert submission.status == SubmissionStatus.APPROVED


async def test_nothing_to_do_is_not_an_error(client, fake_engine):
    assert await worker.run_once(fake_engine) == 0


async def test_claimed_submission_is_not_picked_again(client, fake_engine):
    """Two workers must not take the same submission; the claim's rowcount
    is the guard."""
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()

    async with SessionLocal() as session:
        assert await claim(session, created["id"]) is True
        assert await claim(session, created["id"]) is False, \
            "the second claim should have been refused"

    # It is now verifying, so it must not appear in the queue
    assert await worker.due_submission_ids(10) == []


async def test_retry_waits_for_its_turn(client, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()

    fake_engine.always(engine_down())
    await worker.run_once(fake_engine)

    async with SessionLocal() as session:
        submission = await session.get(Submission, created["id"])
        assert submission.status == SubmissionStatus.PENDING
        assert submission.next_attempt_at > datetime.now(timezone.utc)

    # Not due yet, so the worker should leave it alone
    assert await worker.due_submission_ids(10) == []

    # Move its due time into the past; now it should be picked up
    async with SessionLocal() as session:
        submission = await session.get(Submission, created["id"])
        submission.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    assert await worker.due_submission_ids(10) == [created["id"]]


async def test_batch_size_is_respected(client, fake_engine, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "worker_batch_size", 2)

    campaign = make_campaign(client)
    for i in range(4):
        e = enroll(client, campaign["id"], user=f"user-{i}")
        submit(client, e["id"], user=f"user-{i}",
               url=f"https://www.instagram.com/p/POST{i}/")

    fake_engine.always(engine_result(post_id="X"))
    assert await worker.run_once(fake_engine) == 2


async def test_oldest_first(client, fake_engine):
    campaign = make_campaign(client)
    ids = []
    for i in range(3):
        e = enroll(client, campaign["id"], user=f"user-{i}")
        ids.append(submit(client, e["id"], user=f"user-{i}").json()["id"])

    assert await worker.due_submission_ids(3) == ids


async def test_one_bad_submission_does_not_stop_the_loop(client, fake_engine,
                                                          monkeypatch):
    """One submission crashing must not stop the others."""
    campaign = make_campaign(client)
    for i in range(2):
        e = enroll(client, campaign["id"], user=f"user-{i}")
        submit(client, e["id"], user=f"user-{i}")

    calls = {"n": 0}
    real_process = worker.process

    async def flaky(session, submission, engine):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the first one crashes")
        return await real_process(session, submission, engine)

    monkeypatch.setattr(worker, "process", flaky)
    monkeypatch.setattr("app.config.settings.worker_batch_size", 2)

    fake_engine.always(engine_result(post_id="OK"))
    done = await worker.run_once(fake_engine)

    assert calls["n"] == 2, "the second must still run after the first crashes"
    assert done == 1


async def test_approved_submission_is_not_picked_again(client, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])
    fake_engine.always(engine_result())

    await worker.run_once(fake_engine)
    assert await worker.run_once(fake_engine) == 0


# ---------------- recovering from a dead worker ----------------

async def test_abandoned_submission_returns_to_the_queue(client, fake_engine):
    """A worker that dies mid-verification must not strand its submission.

    claim() moves a row to `verifying`, and only that worker moves it out again.
    If the process is killed in between, nothing would ever pick the row up
    again, because the queue selects only `pending`.
    """
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()

    async with SessionLocal() as session:
        assert await claim(session, created["id"]) is True

    assert await worker.due_submission_ids(10) == [], "it is being worked on"

    # Simulate the worker dying: the lock is now older than the threshold.
    async with SessionLocal() as session:
        submission = await session.get(Submission, created["id"])
        submission.locked_at = datetime.now(timezone.utc) - timedelta(hours=2)
        await session.commit()

    assert await worker.release_stale_locks() == 1
    assert await worker.due_submission_ids(10) == [created["id"]]


async def test_a_slow_verification_is_not_treated_as_abandoned(client, fake_engine):
    """The threshold must exceed a legitimate render, or a slow Instagram
    verification gets picked up a second time while it is still running."""
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()

    async with SessionLocal() as session:
        await claim(session, created["id"])

    assert await worker.release_stale_locks() == 0
    assert await worker.due_submission_ids(10) == []


async def test_recovery_keeps_the_attempt_count(client, fake_engine, monkeypatch):
    """The attempt genuinely happened. Resetting the count would let a crash
    loop retry forever instead of giving up after MAX_ATTEMPTS."""
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()

    async with SessionLocal() as session:
        await claim(session, created["id"])
        submission = await session.get(Submission, created["id"])
        submission.locked_at = datetime.now(timezone.utc) - timedelta(hours=2)
        await session.commit()

    await worker.release_stale_locks()

    async with SessionLocal() as session:
        submission = await session.get(Submission, created["id"])
        assert submission.status == SubmissionStatus.PENDING
        assert submission.attempts == 1
