"""Campaigns aur unke creatives — admin ka hissa, plus user ke liye listing."""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import (APIRouter, Depends, File, Query, Response, UploadFile,
                     status)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..db import get_session
from ..deps import http_error, not_found, require_admin
from ..enums import CampaignStatus
from ..models import Campaign, CampaignAsset
from ..schemas import (AssetOut, CampaignCreate, CampaignOut, CampaignSummary,
                       CampaignUpdate)

router = APIRouter(prefix="/v1/campaigns", tags=["Campaigns"])

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


async def _get(session: AsyncSession, campaign_id: int) -> Campaign:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.assets))
    )
    if campaign is None:
        raise not_found("Campaign")
    return campaign


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)],
             summary="Nayi campaign banao (draft me)")
async def create_campaign(body: CampaignCreate,
                          session: AsyncSession = Depends(get_session)) -> CampaignOut:
    campaign = Campaign(
        title=body.title,
        description=body.description,
        window_hours=body.window_hours,
        allowed_platforms=",".join(body.allowed_platforms)
        if body.allowed_platforms else None,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        status=CampaignStatus.DRAFT,
    )
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign, ["assets"])
    return CampaignOut.model_validate(campaign)


@router.get("", summary="Campaigns ki list")
async def list_campaigns(
    response: Response,
    status_filter: CampaignStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[CampaignSummary]:
    where = [Campaign.status == status_filter] if status_filter else []
    total = await session.scalar(
        select(func.count()).select_from(Campaign).where(*where))
    rows = await session.scalars(
        select(Campaign).where(*where)
        .order_by(Campaign.created_at.desc()).limit(limit).offset(offset)
    )
    response.headers["X-Total-Count"] = str(total or 0)
    return [CampaignSummary.model_validate(c) for c in rows]


@router.get("/{campaign_id}", summary="Ek campaign, uske creatives ke saath")
async def get_campaign(campaign_id: int,
                       session: AsyncSession = Depends(get_session)) -> CampaignOut:
    return CampaignOut.model_validate(await _get(session, campaign_id))


@router.patch("/{campaign_id}", dependencies=[Depends(require_admin)],
              summary="Campaign badlo — activate/close bhi yahin se")
async def update_campaign(campaign_id: int, body: CampaignUpdate,
                          session: AsyncSession = Depends(get_session)) -> CampaignOut:
    campaign = await _get(session, campaign_id)

    if body.status is CampaignStatus.ACTIVE and not campaign.assets:
        # Bina creative ke campaign activate karna matlab har submission
        # no_campaign_assets pe reject hoga — usse pehle hi rok dete hain.
        raise http_error(
            status.HTTP_409_CONFLICT, "no_assets",
            "Campaign activate karne se pehle kam se kam ek image add kijiye")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    await session.commit()
    await session.refresh(campaign, ["assets"])
    return CampaignOut.model_validate(campaign)


@router.post("/{campaign_id}/assets", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)],
             summary="Creative add karo — yahi image user post karega")
async def upload_asset(campaign_id: int, file: UploadFile = File(...),
                       session: AsyncSession = Depends(get_session)) -> AssetOut:
    campaign = await _get(session, campaign_id)

    if file.content_type not in ALLOWED_TYPES:
        raise http_error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "bad_type",
            f"Sirf JPEG, PNG ya WebP chalti hai (mila: {file.content_type})")

    data = await file.read()
    if not data:
        raise http_error(status.HTTP_400_BAD_REQUEST, "empty_file",
                         "File khali hai")
    if len(data) > settings.max_upload_bytes:
        raise http_error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_large",
            f"Image {settings.max_upload_bytes // 1024 // 1024} MB se badi hai")

    digest = hashlib.sha256(data).hexdigest()
    duplicate = await session.scalar(
        select(CampaignAsset).where(CampaignAsset.campaign_id == campaign.id,
                                    CampaignAsset.sha256 == digest)
    )
    if duplicate:
        raise http_error(status.HTTP_409_CONFLICT, "duplicate_asset",
                         "Ye image is campaign me pehle se hai",
                         asset_id=duplicate.id)

    # Content-addressed naam: wahi bytes = wahi file, chahe naam kuch bhi ho.
    suffix = Path(file.filename or "").suffix.lower() or ".jpg"
    directory = settings.assets_dir / str(campaign.id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest[:16]}{suffix}"
    path.write_bytes(data)

    asset = CampaignAsset(
        campaign_id=campaign.id,
        filename=file.filename or path.name,
        content_type=file.content_type,
        size_bytes=len(data),
        sha256=digest,
        storage_path=str(path),
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return AssetOut.model_validate(asset)


@router.get("/{campaign_id}/assets", summary="Campaign ke creatives")
async def list_assets(campaign_id: int,
                      session: AsyncSession = Depends(get_session)) -> list[AssetOut]:
    campaign = await _get(session, campaign_id)
    return [AssetOut.model_validate(a) for a in campaign.assets]


@router.get("/{campaign_id}/assets/{asset_id}/file",
            summary="Creative download karo — yahi image user post karega")
async def download_asset(campaign_id: int, asset_id: int,
                         session: AsyncSession = Depends(get_session)) -> Response:
    asset = await session.get(CampaignAsset, asset_id)
    if asset is None or asset.campaign_id != campaign_id:
        raise not_found("Image")
    try:
        data = Path(asset.storage_path).read_bytes()
    except OSError as e:
        raise http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "file_missing",
                         "Image server pe nahi mili") from e
    return Response(
        data, media_type=asset.content_type,
        headers={"Content-Disposition": f'attachment; filename="{asset.filename}"'},
    )


@router.delete("/{campaign_id}/assets/{asset_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)],
               summary="Creative hatao")
async def delete_asset(campaign_id: int, asset_id: int,
                       session: AsyncSession = Depends(get_session)) -> Response:
    asset = await session.get(CampaignAsset, asset_id)
    if asset is None or asset.campaign_id != campaign_id:
        raise not_found("Image")

    # File rehne dete hain: purane verification records isi sha256 ko point
    # karte hain, aur audit trail ko todna nahi chahiye.
    await session.delete(asset)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
