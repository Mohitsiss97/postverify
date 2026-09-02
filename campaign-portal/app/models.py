"""Data model.

Paanch tables:
    campaigns          admin jo campaign banata hai
    campaign_assets    us campaign ki images (jo user download karke post karega)
    enrollments        kaun sa user kis campaign me hai
    submissions        user ne apne post ka link diya — iska status yahan
    verification_records  har verification attempt ka poora record (audit trail)

Submission aur verification_record alag isliye hain: submission user ko dikhne
wali cheez hai (ek status), aur record har koshish ka saboot hai. Retry hone pe
submission ek hi rehti hai, records badhte jaate hain.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, CheckConstraint, Float, ForeignKey,
                        Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, timestamp_column, utcnow
from .enums import (CampaignStatus, EnrollmentStatus, RejectReason,
                    SubmissionStatus)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(20), default=CampaignStatus.DRAFT,
                                        index=True)

    # Har campaign apna window rakh sakti hai; default settings se aata hai.
    window_hours: Mapped[int] = mapped_column(Integer, default=24)

    # Khali = sab platforms chalte hain. Warna comma separated: "instagram,x"
    allowed_platforms: Mapped[str | None] = mapped_column(String(200), default=None)

    starts_at: Mapped[datetime | None] = timestamp_column(default=None)
    ends_at: Mapped[datetime | None] = timestamp_column(default=None)
    created_at: Mapped[datetime] = timestamp_column(default=utcnow)
    updated_at: Mapped[datetime] = timestamp_column(default=utcnow, onupdate=utcnow)

    assets: Mapped[list["CampaignAsset"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan",
        order_by="CampaignAsset.id")
    enrollments: Mapped[list["Enrollment"]] = relationship(
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
    """Ek creative — jo image user download karke apne account pe post karega."""

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
        # Ek hi image do baar add na ho — galti se duplicate creative banane se bachav
        UniqueConstraint("campaign_id", "sha256", name="uq_asset_per_campaign"),
    )


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)

    # Abhi ye header se aata hai. JWT lagane pe sirf isko bharne ka tareeka
    # badlega, baaki kuch nahi.
    user_ref: Mapped[str] = mapped_column(String(200), index=True)

    status: Mapped[str] = mapped_column(String(20), default=EnrollmentStatus.ACTIVE,
                                        index=True)
    created_at: Mapped[datetime] = timestamp_column(default=utcnow)
    completed_at: Mapped[datetime | None] = timestamp_column(default=None)

    campaign: Mapped[Campaign] = relationship(back_populates="enrollments")
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="enrollment", cascade="all, delete-orphan",
        order_by="Submission.id")

    __table_args__ = (
        UniqueConstraint("campaign_id", "user_ref", name="uq_user_per_campaign"),
    )


class Submission(Base):
    """User ne apne post ka link diya. Verification ka nateeja yahin dikhta hai."""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(
        ForeignKey("enrollments.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)

    post_url: Mapped[str] = mapped_column(String(1000))
    declared_platform: Mapped[str] = mapped_column(String(30))

    # User bata sakta hai ki kaunsa creative post kiya. Na bataye to hum
    # campaign ke creatives ek-ek karke try karte hain (mehenga — har try ek
    # engine call hai), isliye max_assets_to_try se capped.
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaign_assets.id", ondelete="SET NULL"), default=None)

    status: Mapped[str] = mapped_column(String(20), default=SubmissionStatus.PENDING,
                                        index=True)
    reason: Mapped[str | None] = mapped_column(String(40), default=None)
    message: Mapped[str | None] = mapped_column(Text, default=None)

    # Engine ne URL se jo pehchana
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

    # Ek post duniya me ek hi baar gin'i jaye. Ye key sirf tab set hoti hai jab
    # submission zinda ho (pending/verifying/approved); reject hone pe NULL kar
    # dete hain taaki wahi post koi aur — ya wahi user sudhaar ke — bhej sake.
    # NULL values unique index me collide nahi karti, isliye ye portable hai.
    dedupe_key: Mapped[str | None] = mapped_column(String(250), default=None)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = timestamp_column(default=None)
    locked_at: Mapped[datetime | None] = timestamp_column(default=None)

    created_at: Mapped[datetime] = timestamp_column(default=utcnow, index=True)
    updated_at: Mapped[datetime] = timestamp_column(default=utcnow, onupdate=utcnow)
    verified_at: Mapped[datetime | None] = timestamp_column(default=None)

    enrollment: Mapped[Enrollment] = relationship(back_populates="submissions")
    records: Mapped[list["VerificationRecord"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan",
        order_by="VerificationRecord.attempt")

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_live_post"),
        # Worker isi pe queue uthata hai
        Index("ix_submission_queue", "status", "next_attempt_at"),
    )

    @property
    def is_final(self) -> bool:
        return self.status in (SubmissionStatus.APPROVED, SubmissionStatus.REJECTED)


class VerificationRecord(Base):
    """Ek verification attempt ka poora saboot.

    Ye audit trail hai: baad me koi kahe "mera to sahi tha", to yahan se dikhaya
    ja sakta hai ki kis waqt, kaunsi image se, kya nateeja aaya tha.

    Asset ka sha256 yahan copy hota hai — agar admin baad me creative badal de,
    tab bhi record batata hai ki us waqt kis image se compare hua tha.
    """

    __tablename__ = "verification_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)

    outcome: Mapped[str] = mapped_column(String(20))          # approved|rejected|error
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

    # Engine ka poora jawab — jo bhi hum store karna bhool gaye wo yahan hai
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
