"""Admin — sab submissions dekhna, manual faisla, aur dobara check karwana."""
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


@router.get("/submissions", summary="Saari submissions (filter ke saath)")
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
            summary="Ek submission ka poora byora, har attempt ke saath")
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
             summary="Haath se approve ya reject karo")
async def decide(submission_id: int, body: ManualDecision,
                 session: AsyncSession = Depends(get_session)) -> SubmissionOut:
    """Automatic faisle ke upar admin ka faisla.

    Note bhi record me jaata hai — baad me dikhna chahiye ki kisne kya kiya
    aur kyun, warna manual override audit trail me ek khaali khaana ban jaata.
    """
    submission = await session.get(Submission, submission_id)
    if submission is None:
        raise not_found("Submission")
    if submission.status in (SubmissionStatus.PENDING, SubmissionStatus.VERIFYING):
        raise http_error(
            status.HTTP_409_CONFLICT, "still_running",
            "Ye submission abhi check ho rahi hai. Nateeja aane ke baad decide kijiye.")

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
        submission.message = f"Admin ne approve kiya: {body.note}"
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
        submission.message = f"Admin ne reject kiya: {body.note}"
        submission.dedupe_key = None

    submission.verified_at = now
    await session.commit()
    await session.refresh(submission)
    return SubmissionOut.model_validate(submission)


@router.post("/submissions/{submission_id}/recheck",
             status_code=status.HTTP_202_ACCEPTED,
             summary="Dobara queue me daalo")
async def recheck(submission_id: int,
                  session: AsyncSession = Depends(get_session)) -> SubmissionOut:
    """Tab kaam aata hai jab engine down tha, ya post baad me public hui."""
    submission = await session.get(Submission, submission_id)
    if submission is None:
        raise not_found("Submission")
    if submission.status in (SubmissionStatus.PENDING, SubmissionStatus.VERIFYING):
        raise http_error(status.HTTP_409_CONFLICT, "already_queued",
                         "Ye pehle se queue me hai")

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


@router.get("/stats", summary="Campaign ka haal ek nazar me")
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
