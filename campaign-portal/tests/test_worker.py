"""Worker — queue se uthana, do baar na uthana, aur retry ka waqt maanna."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import worker
from app.db import SessionLocal
from app.enums import SubmissionStatus
from app.models import Submission
from app.processing import claim
from tests.conftest import enroll, engine_down, engine_result, make_campaign, submit


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
    """Do worker ek hi submission na uthayein — claim ka rowcount hi guard hai."""
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    created = submit(client, e["id"]).json()

    async with SessionLocal() as session:
        assert await claim(session, created["id"]) is True
        assert await claim(session, created["id"]) is False, \
            "doosri claim ko mana karna chahiye tha"

    # ab wo verifying me hai, to queue me nahi dikhni chahiye
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

    # abhi waqt nahi hua — worker ko ise chhodna chahiye
    assert await worker.due_submission_ids(10) == []

    # ghadi peeche kar do, ab uthni chahiye
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
    """Ek submission ka crash baaki ko nahi rokna chahiye."""
    campaign = make_campaign(client)
    for i in range(2):
        e = enroll(client, campaign["id"], user=f"user-{i}")
        submit(client, e["id"], user=f"user-{i}")

    calls = {"n": 0}
    real_process = worker.process

    async def flaky(session, submission, engine):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("pehla crash")
        return await real_process(session, submission, engine)

    monkeypatch.setattr(worker, "process", flaky)
    monkeypatch.setattr("app.config.settings.worker_batch_size", 2)

    fake_engine.always(engine_result(post_id="OK"))
    done = await worker.run_once(fake_engine)

    assert calls["n"] == 2, "pehla crash hone ke baad doosra bhi chalna chahiye"
    assert done == 1


async def test_approved_submission_is_not_picked_again(client, fake_engine):
    campaign = make_campaign(client)
    e = enroll(client, campaign["id"])
    submit(client, e["id"])
    fake_engine.always(engine_result())

    await worker.run_once(fake_engine)
    assert await worker.run_once(fake_engine) == 0
