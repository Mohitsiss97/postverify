"""The background worker: it takes submissions off the queue and verifies them.

This is separate because verifying an Instagram or Facebook post takes around
fifteen seconds. Holding a participant on an open request for that long is poor
behaviour, and one slow platform would consume all of the application's request
workers. So a submission is saved as `pending` immediately and this loop deals
with it afterwards.

By default it runs inside the application process, which is enough for a small
deployment. Under load, set `worker_enabled=false` and run this same loop as a
separate process (`python -m app.worker`); the code is unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from .config import settings
from .db import SessionLocal
from .engine_client import VerificationEngine, engine_client
from .enums import SubmissionStatus
from .models import Submission
from .processing import claim, process

log = logging.getLogger("portal.worker")


async def release_stale_locks() -> int:
    """Return submissions abandoned by a dead worker to the queue.

    `claim()` moves a submission to `verifying`, and only this worker will move
    it out again. If the process dies in between — a crash, an OOM kill, a
    container stopped before its grace period elapsed — the row stays
    `verifying` forever, because the queue selects only `pending`. The
    participant would wait indefinitely for a result that is never coming.

    The threshold has to comfortably exceed a legitimate verification: a slow
    Instagram render must not be mistaken for a dead worker and picked up a
    second time while it is still running.

    `attempts` is not decremented. The attempt genuinely happened, and letting a
    crash loop retry forever would be worse than giving up after MAX_ATTEMPTS.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.stale_lock_minutes)
    async with SessionLocal() as session:
        result = await session.execute(
            update(Submission)
            .where(Submission.status == SubmissionStatus.VERIFYING,
                   Submission.locked_at.is_not(None),
                   Submission.locked_at < cutoff)
            .values(status=SubmissionStatus.PENDING, locked_at=None,
                    next_attempt_at=None)
        )
        await session.commit()
        if result.rowcount:
            log.warning("returned %s abandoned submission(s) to the queue",
                        result.rowcount)
        return result.rowcount


async def due_submission_ids(limit: int) -> list[int]:
    """Submissions that are due now, oldest first."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(Submission.id)
            .where(
                Submission.status == SubmissionStatus.PENDING,
                or_(Submission.next_attempt_at.is_(None),
                    Submission.next_attempt_at <= now),
            )
            .order_by(Submission.created_at)
            .limit(limit)
        )
        return list(rows)


async def run_once(engine: VerificationEngine | None = None) -> int:
    """One pass over the queue. Returns how many submissions were processed."""
    engine = engine or engine_client
    await release_stale_locks()
    done = 0
    for submission_id in await due_submission_ids(settings.worker_batch_size):
        async with SessionLocal() as session:
            if not await claim(session, submission_id):
                continue        # another worker claimed it first
            submission = await session.get(Submission, submission_id)
            if submission is None:
                continue
            try:
                await process(session, submission, engine)
                done += 1
            except Exception:
                # One submission crashing must not bring down the loop.
                log.exception("crashed while processing submission %s", submission_id)
                await session.rollback()
    return done


async def loop(stop: asyncio.Event) -> None:
    log.info("worker started — up to %d every %.1fs",
             settings.worker_batch_size, settings.worker_poll_seconds)
    while not stop.is_set():
        try:
            processed = await run_once()
        except Exception:
            log.exception("worker loop failed")
            processed = 0
        if processed == 0:
            # Nothing to do: sleep. Otherwise go straight to the next batch.
            try:
                await asyncio.wait_for(stop.wait(), settings.worker_poll_seconds)
            except TimeoutError:
                pass
    log.info("worker stopped")


def main() -> None:
    """Entry point for running as a separate process: python -m app.worker"""
    from .logging_setup import configure_logging
    configure_logging()
    stop = asyncio.Event()
    try:
        asyncio.run(loop(stop))
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
