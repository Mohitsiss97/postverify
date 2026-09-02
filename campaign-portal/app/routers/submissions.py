"""User ka hissa — campaign me enroll karna, aur apna post link submit karna."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..deps import current_user, http_error, not_found
from ..enums import CampaignStatus, SubmissionStatus
from ..models import Campaign, CampaignAsset, Enrollment, Submission
from ..schemas import (EnrollmentOut, SubmissionCreate, SubmissionDetail,
                       SubmissionOut)

router = APIRouter(prefix="/v1", tags=["Submissions"])


# ---------------- enroll ----------------

@router.post("/campaigns/{campaign_id}/enroll", status_code=status.HTTP_201_CREATED,
             summary="Is campaign me shaamil ho")
async def enroll(campaign_id: int, user: str = Depends(current_user),
                 session: AsyncSession = Depends(get_session)) -> EnrollmentOut:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise not_found("Campaign")
    if campaign.status != CampaignStatus.ACTIVE:
        raise http_error(status.HTTP_409_CONFLICT, "campaign_not_active",
                         "Ye campaign abhi chalu nahi hai")

    existing = await session.scalar(
        select(Enrollment).where(Enrollment.campaign_id == campaign_id,
                                 Enrollment.user_ref == user)
    )
    if existing:
        # Dobara enroll karna galti nahi hai — wahi enrollment wapas de do.
        return EnrollmentOut.model_validate(existing)

    enrollment = Enrollment(campaign_id=campaign_id, user_ref=user)
    session.add(enrollment)
    await session.commit()
    await session.refresh(enrollment)
    return EnrollmentOut.model_validate(enrollment)


@router.get("/enrollments", summary="Meri enrollments")
async def my_enrollments(user: str = Depends(current_user),
                         session: AsyncSession = Depends(get_session)
                         ) -> list[EnrollmentOut]:
    rows = await session.scalars(
        select(Enrollment).where(Enrollment.user_ref == user)
        .order_by(Enrollment.created_at.desc())
    )
    return [EnrollmentOut.model_validate(e) for e in rows]


# ---------------- submit ----------------

@router.post("/submissions", status_code=status.HTTP_202_ACCEPTED,
             summary="Apne post ka link submit karo")
async def create_submission(body: SubmissionCreate,
                            user: str = Depends(current_user),
                            session: AsyncSession = Depends(get_session)
                            ) -> SubmissionOut:
    """Submission turant save hoti hai aur `pending` lautati hai.

    Asli verification peeche chalti hai — Instagram/Facebook pe post kholne me
    ~15 second lagte hain, aur user ko itni der roke rakhna theek nahi.
    Status ke liye `GET /v1/submissions/{id}` poll kijiye.
    """
    enrollment = await session.get(Enrollment, body.enrollment_id)
    if enrollment is None:
        raise not_found("Enrollment")
    if enrollment.user_ref != user:
        # 404 jaan-boojh kar — 403 bata deta ki ye id maujood hai.
        raise not_found("Enrollment")

    campaign = await session.get(Campaign, enrollment.campaign_id)
    if campaign is None:
        raise not_found("Campaign")
    if campaign.status != CampaignStatus.ACTIVE:
        raise http_error(status.HTTP_409_CONFLICT, "campaign_not_active",
                         "Ye campaign abhi submissions nahi le rahi")

    if body.asset_id is not None:
        asset = await session.get(CampaignAsset, body.asset_id)
        if asset is None or asset.campaign_id != campaign.id:
            raise not_found("Campaign ki image")

    # Ek hi enrollment pe do submissions ek saath queue me na hon.
    in_flight = await session.scalar(
        select(Submission.id).where(
            Submission.enrollment_id == enrollment.id,
            Submission.status.in_((SubmissionStatus.PENDING,
                                   SubmissionStatus.VERIFYING)),
        ).limit(1)
    )
    if in_flight:
        raise http_error(
            status.HTTP_409_CONFLICT, "already_pending",
            "Aapka ek submission abhi check ho raha hai. Uska nateeja aane "
            "ke baad naya bhejiye.", submission_id=in_flight)

    already_approved = await session.scalar(
        select(Submission.id).where(
            Submission.enrollment_id == enrollment.id,
            Submission.status == SubmissionStatus.APPROVED,
        ).limit(1)
    )
    if already_approved:
        raise http_error(
            status.HTTP_409_CONFLICT, "already_approved",
            "Is campaign ke liye aapka ek submission pehle hi approve ho chuka hai.",
            submission_id=already_approved)

    submission = Submission(
        enrollment_id=enrollment.id,
        campaign_id=campaign.id,
        post_url=body.post_url,
        declared_platform=body.platform,
        asset_id=body.asset_id,
        status=SubmissionStatus.PENDING,
    )
    session.add(submission)
    await session.commit()
    await session.refresh(submission)
    return SubmissionOut.model_validate(submission)


@router.get("/submissions/{submission_id}", summary="Submission ka status")
async def get_submission(submission_id: int, user: str = Depends(current_user),
                         session: AsyncSession = Depends(get_session)
                         ) -> SubmissionDetail:
    submission = await session.scalar(
        select(Submission).where(Submission.id == submission_id)
        .options(selectinload(Submission.records),
                 selectinload(Submission.enrollment))
    )
    if submission is None or submission.enrollment.user_ref != user:
        raise not_found("Submission")
    return SubmissionDetail.model_validate(submission)


@router.get("/submissions", summary="Meri submissions")
async def my_submissions(
    response: Response,
    campaign_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: str = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SubmissionOut]:
    where = [Enrollment.user_ref == user]
    if campaign_id is not None:
        where.append(Submission.campaign_id == campaign_id)

    total = await session.scalar(
        select(func.count()).select_from(Submission)
        .join(Enrollment, Submission.enrollment_id == Enrollment.id).where(*where))
    rows = await session.scalars(
        select(Submission)
        .join(Enrollment, Submission.enrollment_id == Enrollment.id)
        .where(*where)
        .order_by(Submission.created_at.desc()).limit(limit).offset(offset)
    )
    response.headers["X-Total-Count"] = str(total or 0)
    return [SubmissionOut.model_validate(s) for s in rows]
