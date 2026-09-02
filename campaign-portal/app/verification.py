"""Verification rules — ek submission ka faisla yahan hota hai.

Checks ka **order jaan-boojh kar** aisa hai:

    1. campaign chalu hai?          DB, muft
    2. creatives hain?              DB, muft
    3. engine ko ek call            <- yahin se time aur image dono aate hain
    4. platform match               call ke jawab se, muft
    5. 24 ghante ka window          muft — fail ho to yahin ruk jao
    6. duplicate post               DB, muft
    7. image match                  pehli call me ho chuka; na mila to hi aage

Wajah: har engine call Instagram pe ~15 second hai. Time fail ho raha ho to
baaki creatives try karne ka koi matlab nahi — isliye time ka faisla image se
pehle hota hai, chahe dono ek hi call se aaye.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .engine_client import EngineError, EngineResult, VerificationEngine
from .enums import RejectReason, SubmissionStatus, message_for
from .models import Campaign, CampaignAsset, Submission


@dataclass
class Decision:
    """Ek verification attempt ka nateeja."""
    status: SubmissionStatus
    reason: RejectReason | None = None
    message: str | None = None
    result: EngineResult | None = None
    checked_asset: CampaignAsset | None = None
    assets_tried: int = 0
    error_detail: str | None = None
    context: dict = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.status is SubmissionStatus.APPROVED


def _human_age(seconds: int) -> str:
    if seconds < 3600:
        return f"{max(seconds // 60, 1)} minute"
    if seconds < 86_400:
        return f"{seconds // 3600} ghante"
    return f"{seconds // 86_400} din"


def dedupe_key(platform: str, post_id: str) -> str:
    return f"{platform}:{post_id}"


async def _assets_to_try(session: AsyncSession, submission: Submission,
                         campaign: Campaign) -> list[CampaignAsset]:
    """Kaunse creatives se compare karna hai.

    User ne bata diya ho to sirf wahi — ek call, sasta. Na bataya ho to campaign
    ke creatives, par capped: har ek ek engine call hai.
    """
    if submission.asset_id:
        asset = await session.get(CampaignAsset, submission.asset_id)
        if asset and asset.campaign_id == campaign.id:
            return [asset]

    rows = await session.scalars(
        select(CampaignAsset)
        .where(CampaignAsset.campaign_id == campaign.id)
        .order_by(CampaignAsset.id)
        .limit(settings.max_assets_to_try)
    )
    return list(rows)


async def _is_duplicate(session: AsyncSession, submission: Submission,
                        key: str) -> bool:
    """Kya ye post pehle se kisi zinda submission ke naam pe hai?"""
    existing = await session.scalar(
        select(Submission.id).where(
            Submission.dedupe_key == key,
            Submission.id != submission.id,
        ).limit(1)
    )
    return existing is not None


def _read_asset(asset: CampaignAsset) -> bytes:
    return Path(asset.storage_path).read_bytes()


def write_evidence(submission_id: int, attempt: int, payload: dict) -> str | None:
    """Attempt ka poora byora ek file me — DB ke bahar bhi saboot rahe.

    Dispute me yahi dikhaya jaata hai: kis waqt, kaunsi image se, kya jawab aaya.
    """
    try:
        directory = settings.evidence_dir / str(submission_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"attempt-{attempt}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return str(path)
    except OSError:
        # Evidence na likh paana verification ko rokna nahi chahiye — nateeja
        # DB me phir bhi jaata hai.
        return None


async def verify_submission(session: AsyncSession, submission: Submission,
                            engine: VerificationEngine) -> Decision:
    """Ek submission ko poore rules se guzaro aur faisla lao."""
    campaign = await session.get(Campaign, submission.campaign_id)
    if campaign is None:
        return Decision(SubmissionStatus.REJECTED, RejectReason.CAMPAIGN_CLOSED,
                        message_for(RejectReason.CAMPAIGN_CLOSED))

    window_hours = campaign.window_hours or settings.submission_window_hours

    # 1. campaign chalu hai?
    if campaign.status == "closed":
        return Decision(SubmissionStatus.REJECTED, RejectReason.CAMPAIGN_CLOSED,
                        message_for(RejectReason.CAMPAIGN_CLOSED))

    # 2. creatives hain?
    assets = await _assets_to_try(session, submission, campaign)
    if not assets:
        return Decision(SubmissionStatus.REJECTED, RejectReason.NO_CAMPAIGN_ASSETS,
                        message_for(RejectReason.NO_CAMPAIGN_ASSETS))

    tried = 0
    first: EngineResult | None = None

    for asset in assets:
        try:
            data = _read_asset(asset)
        except OSError as e:
            # Creative file gayab — ye humari galti hai, user ki nahi.
            return Decision(SubmissionStatus.ERROR, RejectReason.NO_CAMPAIGN_ASSETS,
                            "Campaign ki image server pe nahi mili",
                            error_detail=str(e), assets_tried=tried)

        try:
            result = await engine.verify(submission.post_url, data,
                                         filename=asset.filename)
        except EngineError as e:
            return Decision(SubmissionStatus.ERROR, e.reason,
                            message_for(e.reason, window=window_hours)
                            if e.reason in (RejectReason.ENGINE_UNAVAILABLE,
                                            RejectReason.TIME_NOT_AVAILABLE)
                            else e.message,
                            checked_asset=asset, assets_tried=tried + 1,
                            error_detail=e.message,
                            context={"engine_status": e.status, "payload": e.payload})

        tried += 1
        if first is None:
            first = result

            # 3. platform: user ne jo kaha, URL wahi hai?
            declared = (submission.declared_platform or "").lower()
            actual = (result.platform or "").lower()
            if declared and actual and declared != actual:
                return Decision(
                    SubmissionStatus.REJECTED, RejectReason.WRONG_PLATFORM,
                    message_for(RejectReason.WRONG_PLATFORM,
                                declared=declared, actual=actual),
                    result=result, checked_asset=asset, assets_tried=tried)

            allowed = campaign.platform_list
            if allowed and actual and actual not in allowed:
                return Decision(
                    SubmissionStatus.REJECTED, RejectReason.WRONG_PLATFORM,
                    f"Is campaign me {actual} allowed nahi hai. "
                    f"Allowed: {', '.join(allowed)}.",
                    result=result, checked_asset=asset, assets_tried=tried)

            # 4. time mila?
            if result.published_at is None:
                return Decision(
                    SubmissionStatus.ERROR, RejectReason.TIME_NOT_AVAILABLE,
                    message_for(RejectReason.TIME_NOT_AVAILABLE, window=window_hours),
                    result=result, checked_asset=asset, assets_tried=tried)

            # 5. window — submit ke waqt se ginte hain, isliye "abhi" se
            age = int((datetime.now(timezone.utc) - result.published_at)
                      .total_seconds())
            if age > window_hours * 3600:
                return Decision(
                    SubmissionStatus.REJECTED, RejectReason.TOO_OLD,
                    message_for(RejectReason.TOO_OLD, age=_human_age(age),
                                window=window_hours),
                    result=result, checked_asset=asset, assets_tried=tried,
                    context={"age_seconds": age, "window_hours": window_hours,
                             "within_window": False})

            # Time pass ho gaya — ye aage har decision ke saath jaana chahiye.
            # Image mismatch pe bhi user ko dikhna chahiye ki timing theek thi.
            time_context = {"age_seconds": age, "window_hours": window_hours,
                            "within_window": True}

            # 6. duplicate — ek post ek hi baar
            if result.platform and result.post_id:
                key = dedupe_key(result.platform, result.post_id)
                if await _is_duplicate(session, submission, key):
                    return Decision(
                        SubmissionStatus.REJECTED, RejectReason.DUPLICATE,
                        message_for(RejectReason.DUPLICATE),
                        result=result, checked_asset=asset, assets_tried=tried,
                        context=time_context)

        # 7. image — pehli call me ho chuka; na mila to agla creative
        if result.image_present:
            return Decision(SubmissionStatus.APPROVED, result=result,
                            checked_asset=asset, assets_tried=tried,
                            message="Sab sahi hai — post time aur image dono match "
                                    "ho gaye.",
                            context=time_context)

    return Decision(
        SubmissionStatus.REJECTED, RejectReason.IMAGE_MISMATCH,
        message_for(RejectReason.IMAGE_MISMATCH),
        result=first, checked_asset=assets[0] if assets else None,
        assets_tried=tried,
        context={**time_context, "assets_available": len(assets)})


def next_retry_at(attempts: int) -> datetime:
    """Exponential backoff — engine down ho to usko saans lene do."""
    delay = settings.retry_base_seconds * (2 ** max(attempts - 1, 0))
    return datetime.now(timezone.utc) + timedelta(seconds=min(delay, 3600))
