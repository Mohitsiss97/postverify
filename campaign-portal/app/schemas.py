"""Request and response shapes.

This is the API contract. models.py is the internal structure; this is the
external one. They are kept separate so that a change to the database does not
break the API.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import CampaignStatus, EnrollmentStatus, SubmissionStatus

PLATFORMS = ("x", "instagram", "facebook", "linkedin", "youtube")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------- campaigns ----------------

class CampaignCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str | None = None
    window_hours: int = Field(24, ge=1, le=24 * 90,
                              description="A post may be at most this many hours old")
    allowed_platforms: list[str] | None = Field(
        None, description="Leave empty to allow every platform")
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @field_validator("allowed_platforms")
    @classmethod
    def known_platforms(cls, value: list[str] | None) -> list[str] | None:
        if not value:
            return None
        cleaned = [p.strip().lower() for p in value if p.strip()]
        unknown = [p for p in cleaned if p not in PLATFORMS]
        if unknown:
            raise ValueError(
                f"These platforms are not supported: {', '.join(unknown)}. "
                f"Valid platforms: {', '.join(PLATFORMS)}")
        return cleaned or None


class CampaignUpdate(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=200)
    description: str | None = None
    status: CampaignStatus | None = None
    window_hours: int | None = Field(None, ge=1, le=24 * 90)


class AssetOut(ORMModel):
    id: int
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


class CampaignOut(ORMModel):
    id: int
    title: str
    description: str | None
    status: str
    window_hours: int
    allowed_platforms: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime
    assets: list[AssetOut] = []


class CampaignSummary(ORMModel):
    id: int
    title: str
    description: str | None
    status: str
    window_hours: int
    created_at: datetime


# ---------------- enrollments ----------------

class EnrollmentOut(ORMModel):
    id: int
    campaign_id: int
    user_ref: str
    status: EnrollmentStatus
    created_at: datetime
    completed_at: datetime | None


# ---------------- submissions ----------------

class SubmissionCreate(BaseModel):
    enrollment_id: int
    post_url: str = Field(..., min_length=8, max_length=1000,
                          examples=["https://www.instagram.com/p/XXXXXXXX/"])
    platform: str = Field(..., examples=["instagram"],
                          description="The platform the participant posted on")
    asset_id: int | None = Field(
        None, description="Which creative was posted. Supplying it means "
                          "verification takes a single engine call; without it "
                          "the campaign's creatives are tried one at a time, "
                          "and each attempt is another call.")

    @field_validator("platform")
    @classmethod
    def known_platform(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in PLATFORMS:
            raise ValueError(
                f"'{value}' is not supported. Valid platforms: "
                f"{', '.join(PLATFORMS)}")
        return cleaned

    @field_validator("post_url")
    @classmethod
    def looks_like_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.lower().startswith(("http://", "https://")):
            raise ValueError("Enter the full post URL, starting with https://")
        return cleaned


class SubmissionOut(ORMModel):
    id: int
    campaign_id: int
    enrollment_id: int
    post_url: str
    declared_platform: str
    status: SubmissionStatus
    reason: str | None
    message: str | None

    resolved_platform: str | None
    post_id: str | None
    canonical_url: str | None
    published_at: datetime | None
    age_seconds: int | None
    within_window: bool | None

    matched_asset_id: int | None
    image_verdict: str | None
    image_score: int | None

    attempts: int
    created_at: datetime
    verified_at: datetime | None


class RecordOut(ORMModel):
    id: int
    attempt: int
    outcome: str
    reason: str | None
    checked_asset_id: int | None
    checked_asset_sha256: str | None
    assets_tried: int
    published_at: datetime | None
    age_seconds: int | None
    window_hours: int | None
    within_window: bool | None
    image_verdict: str | None
    image_score: int | None
    engine_status: int | None
    duration_ms: int | None
    evidence_path: str | None
    error_detail: str | None
    checked_at: datetime


class SubmissionDetail(SubmissionOut):
    """The full account, including the record of every attempt, for auditing."""
    records: list[RecordOut] = []


class ManualDecision(BaseModel):
    approve: bool
    note: str = Field(..., min_length=3, max_length=500,
                      description="The reason for the decision; it is recorded")


class Page(BaseModel):
    total: int
    limit: int
    offset: int
