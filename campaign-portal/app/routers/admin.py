"""Administration: reviewing every submission, deciding one by hand, and
requeueing one for another check."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..deps import http_error, not_found, require_admin
from ..enums import EnrollmentStatus, RejectReason, SubmissionStatus
from ..models import Enrollment, Submission, VerificationRecord
from ..schemas import ManualDecision, SubmissionDetail, SubmissionOut
from ..verification import dedupe_key

router = APIRouter(prefix="/v1/admin", tags=["Admin"],
                   dependencies=[Depends(require_admin)])


@router.get("/submissions", summary="All submissions, with filters")
async def list_submissions(
    response: Response,
    campaign_id: int | None = Query(None),
    status_filter: SubmissionStatus | None = Query(None, alias="status"),
    reason: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[SubmissionOut]:
    where = []
    if campaign_id is not None:
        where.append(Submission.campaign_id == campaign_id)
    if status_filter is not None:
        where.append(Submission.status == status_filter)
    if reason:
        where.append(Submission.reason == reason)

    total = await session.scalar(
        select(func.count()).select_from(Submission).where(*where))
    rows = await session.scalars(
        select(Submission).where(*where)
        .order_by(Submission.created_at.desc()).limit(limit).offset(offset)
    )
    response.headers["X-Total-Count"] = str(total or 0)
    return [SubmissionOut.model_validate(s) for s in rows]


@router.get("/submissions/{submission_id}",
            summary="One submission in full, with every attempt")
async def get_submission(submission_id: int,
                         session: AsyncSession = Depends(get_session)
                         ) -> SubmissionDetail:
    submission = await session.scalar(
        select(Submission).where(Submission.id == submission_id)
        .options(selectinload(Submission.records))
    )
    if submission is None:
        raise not_found("Submission")
    return SubmissionDetail.model_validate(submission)


@router.post("/submissions/{submission_id}/decide",
             summary="Approve or reject by hand")
async def decide(submission_id: int, body: ManualDecision,
                 session: AsyncSession = Depends(get_session)) -> SubmissionOut:
    """An administrator's decision, overriding the automatic one.

    The note is written into the record as well. Without it a manual override
    would be a blank space in the audit trail, and later it must be possible to
    see what was done and why.
    """
    submission = await session.get(Submission, submission_id)
    if submission is None:
        raise not_found("Submission")
    if submission.status in (SubmissionStatus.PENDING, SubmissionStatus.VERIFYING):
        raise http_error(
            status.HTTP_409_CONFLICT, "still_running",
            "This submission is still being checked. Decide once the result "
            "is in.")

    now = datetime.now(timezone.utc)
    submission.attempts += 1
    session.add(VerificationRecord(
        submission_id=submission.id,
        attempt=submission.attempts,
        outcome=str(SubmissionStatus.APPROVED if body.approve
                    else SubmissionStatus.REJECTED),
        reason=None if body.approve else str(RejectReason.MANUAL_REJECT),
        error_detail=f"manual: {body.note}",
        checked_at=now,
    ))

    if body.approve:
        submission.status = SubmissionStatus.APPROVED
        submission.reason = None
        submission.message = f"Approved by an administrator: {body.note}"
        if submission.resolved_platform and submission.post_id:
            submission.dedupe_key = dedupe_key(submission.resolved_platform,
                                                submission.post_id)
        enrollment = await session.get(Enrollment, submission.enrollment_id)
        if enrollment:
            enrollment.status = EnrollmentStatus.COMPLETED
            enrollment.completed_at = now
    else:
        submission.status = SubmissionStatus.REJECTED
        submission.reason = str(RejectReason.MANUAL_REJECT)
        submission.message = f"Rejected by an administrator: {body.note}"
        submission.dedupe_key = None

    submission.verified_at = now
    await session.commit()
    await session.refresh(submission)
    return SubmissionOut.model_validate(submission)


@router.post("/submissions/{submission_id}/recheck",
             status_code=status.HTTP_202_ACCEPTED,
             summary="Put it back in the queue")
async def recheck(submission_id: int,
                  session: AsyncSession = Depends(get_session)) -> SubmissionOut:
    """Useful when the engine was down, or when the post was made public later."""
    submission = await session.get(Submission, submission_id)
    if submission is None:
        raise not_found("Submission")
    if submission.status in (SubmissionStatus.PENDING, SubmissionStatus.VERIFYING):
        raise http_error(status.HTTP_409_CONFLICT, "already_queued",
                         "This is already in the queue")

    submission.status = SubmissionStatus.PENDING
    submission.reason = None
    submission.message = None
    submission.next_attempt_at = None
    submission.locked_at = None
    submission.verified_at = None
    submission.dedupe_key = None
    await session.commit()
    await session.refresh(submission)
    return SubmissionOut.model_validate(submission)


@router.get("/stats", summary="Campaign health at a glance")
async def stats(campaign_id: int | None = Query(None),
                session: AsyncSession = Depends(get_session)) -> dict:
    where = [Submission.campaign_id == campaign_id] if campaign_id else []

    by_status = await session.execute(
        select(Submission.status, func.count()).where(*where)
        .group_by(Submission.status))
    by_reason = await session.execute(
        select(Submission.reason, func.count())
        .where(*where, Submission.reason.is_not(None))
        .group_by(Submission.reason))

    return {
        "campaign_id": campaign_id,
        "by_status": {row[0]: row[1] for row in by_status},
        "by_reject_reason": {row[0]: row[1] for row in by_reason},
    }
