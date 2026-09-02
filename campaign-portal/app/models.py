"""The data model.

Five tables:
    campaigns             the campaigns an administrator creates
    campaign_assets       their creatives, which participants download and post
    enrollments           which participant is in which campaign
    submissions           a participant's post link, and its outcome
    verification_records  the full record of every verification attempt

Submission and verification_record are separate on purpose. The submission is
what the participant sees — one row with one status. A record is the evidence of
one attempt. Across retries the submission stays a single row while the records
accumulate.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, timestamp_column, utcnow
from .enums import CampaignStatus, EnrollmentStatus, SubmissionStatus


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(20), default=CampaignStatus.DRAFT,
                                        index=True)

    # Each campaign may set its own window; the default comes from settings.
    window_hours: Mapped[int] = mapped_column(Integer, default=24)

    # Empty means every platform is allowed. Otherwise comma-separated:
    # "instagram,x"
    allowed_platforms: Mapped[str | None] = mapped_column(String(200), default=None)

    starts_at: Mapped[datetime | None] = timestamp_column(default=None)
    ends_at: Mapped[datetime | None] = timestamp_column(default=None)
    created_at: Mapped[datetime] = timestamp_column(default=utcnow)
    updated_at: Mapped[datetime] = timestamp_column(default=utcnow, onupdate=utcnow)

    assets: Mapped[list[CampaignAsset]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan",
        order_by="CampaignAsset.id")
    enrollments: Mapped[list[Enrollment]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("window_hours > 0", name="window_positive"),
    )

    @property
    def platform_list(self) -> list[str]:
        if not self.allowed_platforms:
            return []
        return [p.strip() for p in self.allowed_platforms.split(",") if p.strip()]


class CampaignAsset(Base):
    """One creative: the image a participant downloads and posts to their own
    account."""

    __tablename__ = "campaign_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)

    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = timestamp_column(default=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="assets")

    __table_args__ = (
        # The same image cannot be added twice, which guards against creating a
        # duplicate creative by accident.
        UniqueConstraint("campaign_id", "sha256", name="uq_asset_per_campaign"),
    )


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)

    # For now this comes from a header. Introducing JWT changes only how this
    # column is populated, and nothing else.
    user_ref: Mapped[str] = mapped_column(String(200), index=True)

    status: Mapped[str] = mapped_column(String(20), default=EnrollmentStatus.ACTIVE,
                                        index=True)
    created_at: Mapped[datetime] = timestamp_column(default=utcnow)
    completed_at: Mapped[datetime | None] = timestamp_column(default=None)

    campaign: Mapped[Campaign] = relationship(back_populates="enrollments")
    submissions: Mapped[list[Submission]] = relationship(
        back_populates="enrollment", cascade="all, delete-orphan",
        order_by="Submission.id")

    __table_args__ = (
        UniqueConstraint("campaign_id", "user_ref", name="uq_user_per_campaign"),
    )


class Submission(Base):
    """A participant's post link, and the outcome of verifying it."""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(
        ForeignKey("enrollments.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)

    post_url: Mapped[str] = mapped_column(String(1000))
    declared_platform: Mapped[str] = mapped_column(String(30))

    # The participant may state which creative they posted. If they do not, the
    # campaign's creatives are tried one by one. That is expensive — each
    # attempt is a separate engine call — so it is capped by max_assets_to_try.
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaign_assets.id", ondelete="SET NULL"), default=None)

    status: Mapped[str] = mapped_column(String(20), default=SubmissionStatus.PENDING,
                                        index=True)
    reason: Mapped[str | None] = mapped_column(String(40), default=None)
    message: Mapped[str | None] = mapped_column(Text, default=None)

    # What the engine resolved from the URL
    resolved_platform: Mapped[str | None] = mapped_column(String(30), default=None)
    post_id: Mapped[str | None] = mapped_column(String(200), default=None)
    canonical_url: Mapped[str | None] = mapped_column(String(1000), default=None)
    published_at: Mapped[datetime | None] = timestamp_column(default=None)
    age_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    within_window: Mapped[bool | None] = mapped_column(Boolean, default=None)

    matched_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaign_assets.id", ondelete="SET NULL"), default=None)
    image_score: Mapped[int | None] = mapped_column(Integer, default=None)
    image_verdict: Mapped[str | None] = mapped_column(String(20), default=None)

    # A given post counts exactly once. This key is only set while the
    # submission is live (pending, verifying or approved); on rejection it is set
    # back to NULL so that the same post can be submitted again — by someone
    # else, or by the same participant after fixing the problem. NULLs do not
    # collide in a unique index, which makes this portable across databases.
    dedupe_key: Mapped[str | None] = mapped_column(String(250), default=None)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = timestamp_column(default=None)
    locked_at: Mapped[datetime | None] = timestamp_column(default=None)

    created_at: Mapped[datetime] = timestamp_column(default=utcnow, index=True)
    updated_at: Mapped[datetime] = timestamp_column(default=utcnow, onupdate=utcnow)
    verified_at: Mapped[datetime | None] = timestamp_column(default=None)

    enrollment: Mapped[Enrollment] = relationship(back_populates="submissions")
    records: Mapped[list[VerificationRecord]] = relationship(
        back_populates="submission", cascade="all, delete-orphan",
        order_by="VerificationRecord.attempt")

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_live_post"),
        # The index the worker reads the queue on
        Index("ix_submission_queue", "status", "next_attempt_at"),
    )

    @property
    def is_final(self) -> bool:
        return self.status in (SubmissionStatus.APPROVED, SubmissionStatus.REJECTED)


class VerificationRecord(Base):
    """The full evidence for one verification attempt.

    This is the audit trail. When a participant later disputes an outcome, it
    shows exactly when the check ran, which image it compared against, and what
    the result was.

    The asset's SHA-256 is copied here deliberately: if an administrator later
    replaces the creative, the record still says which image was actually used
    at the time.
    """

    __tablename__ = "verification_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)

    outcome: Mapped[str] = mapped_column(String(20))   # approved | rejected | error
    reason: Mapped[str | None] = mapped_column(String(40), default=None)

    checked_asset_id: Mapped[int | None] = mapped_column(Integer, default=None)
    checked_asset_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    assets_tried: Mapped[int] = mapped_column(Integer, default=0)

    published_at: Mapped[datetime | None] = timestamp_column(default=None)
    age_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    window_hours: Mapped[int | None] = mapped_column(Integer, default=None)
    within_window: Mapped[bool | None] = mapped_column(Boolean, default=None)

    image_verdict: Mapped[str | None] = mapped_column(String(20), default=None)
    image_score: Mapped[int | None] = mapped_column(Integer, default=None)
    image_present: Mapped[bool | None] = mapped_column(Boolean, default=None)

    # The engine's complete response: whatever the columns above do not
    # capture is still here.
    engine_response: Mapped[dict | None] = mapped_column(JSON, default=None)
    engine_status: Mapped[int | None] = mapped_column(Integer, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    evidence_path: Mapped[str | None] = mapped_column(String(500), default=None)

    error_detail: Mapped[str | None] = mapped_column(Text, default=None)
    checked_at: Mapped[datetime] = timestamp_column(default=utcnow)

    submission: Mapped[Submission] = relationship(back_populates="records")

    __table_args__ = (
        UniqueConstraint("submission_id", "attempt", name="uq_attempt"),
    )
