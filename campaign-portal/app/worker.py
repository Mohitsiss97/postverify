"""Background worker — submissions ko queue se uthakar verify karta hai.

Ye alag isliye hai ki Instagram/Facebook verify me ~15 second lagte hain. User ko
itni der latka ke rakhna theek nahi, aur ek slow platform poore app ke request
workers kha jaata hai. To submission turant `pending` me save hoti hai, aur ye
loop use peeche se nipta deta hai.

Abhi ye app ke andar hi chalta hai — ek chhote deployment ke liye kaafi. Load
badhe to `worker_enabled=false` karke isi loop ko alag process me chalaya ja
sakta hai (`python -m app.worker`); code wahi rehta hai.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select

from .config import settings
from .db import SessionLocal
from .engine_client import VerificationEngine, engine_client
from .enums import SubmissionStatus
from .models import Submission
from .processing import claim, process

log = logging.getLogger("portal.worker")


async def due_submission_ids(limit: int) -> list[int]:
    """Jo ab chalne layak hain — pehle purani."""
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
    """Ek chakkar — kitni submissions nipti, wo lautata hai."""
    engine = engine or engine_client
    done = 0
    for submission_id in await due_submission_ids(settings.worker_batch_size):
        async with SessionLocal() as session:
            if not await claim(session, submission_id):
                continue        # kisi aur worker ne pehle le liya
            submission = await session.get(Submission, submission_id)
            if submission is None:
                continue
            try:
                await process(session, submission, engine)
                done += 1
            except Exception:
                # Ek submission ka crash poore loop ko na giraye.
                log.exception("submission %s process karte waqt crash", submission_id)
                await session.rollback()
    return done


async def loop(stop: asyncio.Event) -> None:
    log.info("worker chalu — har %.1fs me %d tak",
             settings.worker_poll_seconds, settings.worker_batch_size)
    while not stop.is_set():
        try:
            processed = await run_once()
        except Exception:
            log.exception("worker loop me dikkat")
            processed = 0
        if processed == 0:
            # Kuch nahi mila to thoda so jao; mila to turant agla batch.
            try:
                await asyncio.wait_for(stop.wait(), settings.worker_poll_seconds)
            except asyncio.TimeoutError:
                pass
    log.info("worker band")


def main() -> None:
    """Alag process me chalane ke liye: python -m app.worker"""
    from .logging_setup import configure_logging
    configure_logging()
    stop = asyncio.Event()
    try:
        asyncio.run(loop(stop))
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
