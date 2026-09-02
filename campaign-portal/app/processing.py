"""Writing a decision to the database, and deciding whether to retry.

Two things are kept apart here:
    submission          the single status a participant sees
    verification_record the evidence for one attempt

Retries happen only on technical failures — the engine being down, or a timeout.
A business rejection is final: running "the image did not match" a second time
will not produce a different answer, and every retry costs another fifteen-second
render on Instagram.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .engine_client import VerificationEngine
from .enums import RETRYABLE, EnrollmentStatus, RejectReason, SubmissionStatus, message_for
from .models import Enrollment, Submission, VerificationRecord
from .verification import (
    Decision,
    dedupe_key,
    next_retry_at,
    verify_submission,
    write_evidence,
)

log = logging.getLogger("portal.processing")


async def claim(session: AsyncSession, submission_id: int) -> bool:
    """Move a submission to `verifying`, but only if it is still pending.

    This guard is what stops two workers taking the same submission: a rowcount
    of 0 from the UPDATE means someone else claimed it first.
    """
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(Submission)
        .where(Submission.id == submission_id,
               Submission.status == SubmissionStatus.PENDING)
        .values(status=SubmissionStatus.VERIFYING, locked_at=now,
                attempts=Submission.attempts + 1, updated_at=now)
    )
    await session.commit()
    return result.rowcount == 1


def _record_from(submission: Submission, decision: Decision) -> VerificationRecord:
    result = decision.result
    asset = decision.checked_asset
    payload = {
        "submission_id": submission.id,
        "attempt": submission.attempts,
        "post_url": submission.post_url,
        "declared_platform": submission.declared_platform,
        "checked_asset": {
            "id": asset.id if asset else None,
            "filename": asset.filename if asset else None,
            "sha256": asset.sha256 if asset else None,
        },
        "outcome": str(decision.status),
        "reason": str(decision.reason) if decision.reason else None,
        "engine_response": result.raw if result else None,
        "context": decision.context,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    evidence_path = write_evidence(submission.id, submission.attempts, payload)

    return VerificationRecord(
        submission_id=submission.id,
        attempt=submission.attempts,
        outcome=str(decision.status),
        reason=str(decision.reason) if decision.reason else None,
        checked_asset_id=asset.id if asset else None,
        checked_asset_sha256=asset.sha256 if asset else None,
        assets_tried=decision.assets_tried,
        published_at=result.published_at if result else None,
        age_seconds=decision.context.get("age_seconds")
        or (result.age_seconds if result else None),
        window_hours=decision.context.get("window_hours"),
        within_window=decision.context.get("within_window"),
        image_verdict=result.image_verdict if result else None,
        image_score=result.image_score if result else None,
        image_present=result.image_present if result else None,
        engine_response=result.raw if result else None,
        engine_status=result.status_code if result else decision.context.get(
            "engine_status"),
        duration_ms=result.duration_ms if result else None,
        evidence_path=evidence_path,
        error_detail=decision.error_detail,
    )


def _apply(submission: Submission, decision: Decision) -> None:
    """Set the submission's fields according to the decision."""
    result = decision.result
    now = datetime.now(timezone.utc)

    if result:
        submission.resolved_platform = result.platform
        submission.post_id = result.post_id
        submission.canonical_url = result.canonical_url
        submission.published_at = result.published_at
        submission.age_seconds = decision.context.get("age_seconds",
                                                      result.age_seconds)
        submission.image_score = result.image_score
        submission.image_verdict = result.image_verdict
    if decision.checked_asset and decision.approved:
        submission.matched_asset_id = decision.checked_asset.id

    if "within_window" in decision.context:
        submission.within_window = decision.context["within_window"]

    submission.reason = str(decision.reason) if decision.reason else None
    submission.message = decision.message
    submission.updated_at = now

    if decision.approved:
        submission.status = SubmissionStatus.APPROVED
        submission.verified_at = now
        # dedupe_key only exists on live submissions. It is cleared on
        # rejection so the same post can be submitted again, by someone else or
        # by the same participant after fixing the problem.
        if result and result.platform and result.post_id:
            submission.dedupe_key = dedupe_key(result.platform, result.post_id)
        return

    submission.dedupe_key = None

    if decision.status is SubmissionStatus.REJECTED:
        submission.status = SubmissionStatus.REJECTED
        submission.verified_at = now
        return

    # ERROR: either retry, or give up and record the failure
    retryable = decision.reason in RETRYABLE
    if retryable and submission.attempts < settings.max_attempts:
        submission.status = SubmissionStatus.PENDING
        submission.next_attempt_at = next_retry_at(submission.attempts)
        submission.locked_at = None
    else:
        submission.status = SubmissionStatus.ERROR
        submission.verified_at = now


async def _complete_enrollment(session: AsyncSession, submission: Submission) -> None:
    enrollment = await session.get(Enrollment, submission.enrollment_id)
    if enrollment and enrollment.status != EnrollmentStatus.COMPLETED:
        enrollment.status = EnrollmentStatus.COMPLETED
        enrollment.completed_at = datetime.now(timezone.utc)


async def process(session: AsyncSession, submission: Submission,
                  engine: VerificationEngine) -> Submission:
    """Verify one claimed submission and write down the outcome."""
    decision = await verify_submission(session, submission, engine)

    session.add(_record_from(submission, decision))
    _apply(submission, decision)

    if decision.approved:
        await _complete_enrollment(session, submission)

    try:
        await session.commit()
    except IntegrityError:
        # The unique index on dedupe_key fired, which means two submissions
        # raced to be approved for the same post. The one that lost is told it
        # is a duplicate.
        await session.rollback()
        await session.refresh(submission)
        submission.status = SubmissionStatus.REJECTED
        submission.reason = str(RejectReason.DUPLICATE)
        submission.message = message_for(RejectReason.DUPLICATE)
        submission.dedupe_key = None
        submission.verified_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("submission %s lost the duplicate race", submission.id)

    log.info("submission %s -> %s (%s, attempt %s, %s assets tried)",
             submission.id, submission.status, submission.reason or "-",
             submission.attempts, decision.assets_tried)
    return submission
