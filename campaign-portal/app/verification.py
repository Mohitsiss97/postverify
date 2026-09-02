"""The verification rules. A submission's outcome is decided here.

The **order of the checks is deliberate**:

    1. is the campaign open?      database, free
    2. does it have creatives?    database, free
    3. one call to the engine     <- this returns both the time and the image
    4. platform match             from that response, free
    5. the 24-hour window         free — stop here if it fails
    6. duplicate post             database, free
    7. image match                already done by the first call; only if it
                                  fails do we try another creative

The reason is cost: an engine call against Instagram takes about fifteen
seconds. If the timing already disqualifies the post, trying further creatives
is pure waste — so the timing decision is made before the image decision even
though a single call produced both.
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
from .enums import REASON_MESSAGES, RETRYABLE, RejectReason, SubmissionStatus, message_for
from .models import Campaign, CampaignAsset, Submission


@dataclass
class Decision:
    """The outcome of one verification attempt."""
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
        count = max(seconds // 60, 1)
        return f"{count} minute{'s' if count != 1 else ''}"
    if seconds < 86_400:
        count = seconds // 3600
        return f"{count} hour{'s' if count != 1 else ''}"
    count = seconds // 86_400
    return f"{count} day{'s' if count != 1 else ''}"


def dedupe_key(platform: str, post_id: str) -> str:
    return f"{platform}:{post_id}"


async def _assets_to_try(session: AsyncSession, submission: Submission,
                         campaign: Campaign) -> list[CampaignAsset]:
    """Which creatives to compare against.

    If the participant said which one they posted, only that one is used — a
    single call, and cheap. Otherwise the campaign's creatives are tried, capped,
    because each one is another engine call.
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
    """Is this post already claimed by another live submission?"""
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
    """Write the full account of one attempt to a file, outside the database.

    This is what gets shown in a dispute: when the check ran, which image it used
    and what came back.
    """
    try:
        directory = settings.evidence_dir / str(submission_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"attempt-{attempt}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return str(path)
    except OSError:
        # Failing to write evidence must not fail the verification; the outcome
        # still reaches the database either way.
        return None


async def verify_submission(session: AsyncSession, submission: Submission,
                            engine: VerificationEngine) -> Decision:
    """Run one submission through every rule and return the decision."""
    campaign = await session.get(Campaign, submission.campaign_id)
    if campaign is None:
        return Decision(SubmissionStatus.REJECTED, RejectReason.CAMPAIGN_CLOSED,
                        message_for(RejectReason.CAMPAIGN_CLOSED))

    window_hours = campaign.window_hours or settings.submission_window_hours

    # 1. is the campaign open?
    if campaign.status == "closed":
        return Decision(SubmissionStatus.REJECTED, RejectReason.CAMPAIGN_CLOSED,
                        message_for(RejectReason.CAMPAIGN_CLOSED))

    # 2. does it have creatives?
    assets = await _assets_to_try(session, submission, campaign)
    if not assets:
        return Decision(SubmissionStatus.REJECTED, RejectReason.NO_CAMPAIGN_ASSETS,
                        message_for(RejectReason.NO_CAMPAIGN_ASSETS))

    tried = 0
    first: EngineResult | None = None
    # Once the timing check passes, this travels with every subsequent decision.
    # On an image mismatch the participant should still be able to see that the
    # timing was fine.
    time_context: dict = {}

    for asset in assets:
        try:
            data = _read_asset(asset)
        except OSError as e:
            # A missing creative file is our fault, not the participant's.
            return Decision(SubmissionStatus.ERROR, RejectReason.NO_CAMPAIGN_ASSETS,
                            "The campaign image could not be read on the server",
                            error_detail=str(e), assets_tried=tried)

        try:
            result = await engine.verify(
                submission.post_url, data, filename=asset.filename,
                request_id=f"sub-{submission.id}-a{submission.attempts}")
        except EngineError as e:
            # `error` is the status operators watch to judge whether the system
            # itself is healthy, so only a technical failure may claim it. An
            # unsupported URL or a private post is the participant's mistake and
            # ends as `rejected`, which is equally final but does not look like
            # an outage on the stats page.
            technical = e.reason in RETRYABLE
            return Decision(SubmissionStatus.ERROR if technical
                            else SubmissionStatus.REJECTED,
                            e.reason,
                            message_for(e.reason, window=window_hours)
                            if e.reason in REASON_MESSAGES else e.message,
                            checked_asset=asset, assets_tried=tried + 1,
                            error_detail=e.message,
                            context={"engine_status": e.status, "payload": e.payload})

        tried += 1
        if first is None:
            first = result

            # 3. platform: does the URL match what the participant selected?
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
                    f"{actual} is not allowed in this campaign. "
                    f"Allowed platforms: {', '.join(allowed)}.",
                    result=result, checked_asset=asset, assets_tried=tried)

            # 4. was a publish time found at all?
            if result.published_at is None:
                return Decision(
                    SubmissionStatus.ERROR, RejectReason.TIME_NOT_AVAILABLE,
                    message_for(RejectReason.TIME_NOT_AVAILABLE, window=window_hours),
                    result=result, checked_asset=asset, assets_tried=tried)

            # 5. the window, measured from now, because it is counted from the
            #    moment of submission rather than from enrolment
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

            time_context = {"age_seconds": age, "window_hours": window_hours,
                            "within_window": True}

            # 6. duplicate: a post counts exactly once
            if result.platform and result.post_id:
                key = dedupe_key(result.platform, result.post_id)
                if await _is_duplicate(session, submission, key):
                    return Decision(
                        SubmissionStatus.REJECTED, RejectReason.DUPLICATE,
                        message_for(RejectReason.DUPLICATE),
                        result=result, checked_asset=asset, assets_tried=tried,
                        context=time_context)

        # 7. the image, already compared by the call above. If it did not match,
        #    move on to the next creative.
        if result.image_present:
            return Decision(SubmissionStatus.APPROVED, result=result,
                            checked_asset=asset, assets_tried=tried,
                            message="Everything checks out — both the posting "
                                    "time and the image matched.",
                            context=time_context)

    return Decision(
        SubmissionStatus.REJECTED, RejectReason.IMAGE_MISMATCH,
        message_for(RejectReason.IMAGE_MISMATCH),
        result=first, checked_asset=assets[0] if assets else None,
        assets_tried=tried,
        context={**time_context, "assets_available": len(assets)})


def next_retry_at(attempts: int) -> datetime:
    """Exponential backoff, so a struggling engine is given room to recover."""
    delay = settings.retry_base_seconds * (2 ** max(attempts - 1, 0))
    return datetime.now(timezone.utc) + timedelta(seconds=min(delay, 3600))
