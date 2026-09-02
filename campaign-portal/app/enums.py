"""Statuses and rejection reasons.

The reason codes are what the participant actually sees, so they are kept in one
place: the API, the database and the user-facing message all come from here.
A new reason is added here and nowhere else.
"""
from __future__ import annotations

from enum import StrEnum


class CampaignStatus(StrEnum):
    DRAFT = "draft"          # being prepared; not visible to participants
    ACTIVE = "active"        # running; enrolment and submission are open
    CLOSED = "closed"        # finished; no new submissions accepted


class EnrollmentStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"  # one submission has been approved
    CANCELLED = "cancelled"


class SubmissionStatus(StrEnum):
    PENDING = "pending"      # queued, waiting for a worker
    VERIFYING = "verifying"  # a worker is processing it now
    APPROVED = "approved"    # every check passed
    REJECTED = "rejected"    # a rule failed; final, never retried
    ERROR = "error"          # a technical failure that exhausted its retries


class RejectReason(StrEnum):
    """Why a submission was rejected.

    Most of these are business rules and are final. ENGINE_UNAVAILABLE and
    TIME_NOT_AVAILABLE are technical and are retried; see RETRYABLE below.
    """

    TOO_OLD = "too_old"
    IMAGE_MISMATCH = "image_mismatch"
    WRONG_PLATFORM = "wrong_platform"
    DUPLICATE = "duplicate"
    POST_NOT_FOUND = "post_not_found"
    NO_IMAGE_IN_POST = "no_image_in_post"
    UNSUPPORTED_URL = "unsupported_url"
    TIME_NOT_AVAILABLE = "time_not_available"
    NO_CAMPAIGN_ASSETS = "no_campaign_assets"
    CAMPAIGN_CLOSED = "campaign_closed"
    MANUAL_REJECT = "manual_reject"
    ENGINE_UNAVAILABLE = "engine_unavailable"


# What the participant is shown. Plain language, no accusation, and wherever
# possible the next step they can take.
REASON_MESSAGES: dict[RejectReason, str] = {
    RejectReason.TOO_OLD:
        "This post is {age} old. Posts must be published within {window} hours — "
        "post it again and submit the new link.",
    RejectReason.IMAGE_MISMATCH:
        "The image in the post does not match the campaign creative. Please check "
        "that you posted the same image you downloaded here.",
    RejectReason.WRONG_PLATFORM:
        "You selected {declared}, but this link is a {actual} link. "
        "Select the correct platform and submit again.",
    RejectReason.DUPLICATE:
        "This post has already been submitted. Each post counts only once.",
    RejectReason.POST_NOT_FOUND:
        "The post could not be opened — it may be private or deleted. "
        "Make the post public and try again.",
    RejectReason.NO_IMAGE_IN_POST:
        "No image was found in this post. Please post the campaign image with it.",
    RejectReason.UNSUPPORTED_URL:
        "This does not look like a link from a supported platform. Paste the full "
        "post URL from Instagram, Facebook, X, LinkedIn or YouTube.",
    RejectReason.TIME_NOT_AVAILABLE:
        "The post's upload time could not be read, so the {window}-hour check "
        "could not be completed. Please try again shortly.",
    RejectReason.NO_CAMPAIGN_ASSETS:
        "This campaign has no creative yet. Please contact the administrator.",
    RejectReason.CAMPAIGN_CLOSED:
        "This campaign has closed and is no longer accepting submissions.",
    RejectReason.MANUAL_REJECT:
        "This submission was rejected during review.",
    RejectReason.ENGINE_UNAVAILABLE:
        "The verification service is not responding. Your submission is safe and "
        "will be checked again shortly.",
}

# Retrying only makes sense for a technical failure. Everything else is a
# judgement that will not change on a second attempt.
RETRYABLE: frozenset[RejectReason] = frozenset({
    RejectReason.ENGINE_UNAVAILABLE,
    RejectReason.TIME_NOT_AVAILABLE,
})


def message_for(reason: RejectReason, **context: object) -> str:
    template = REASON_MESSAGES[reason]
    try:
        return template.format(**context)
    except KeyError:
        # Incomplete context: better to show the template without its
        # placeholders filled than a half-rendered message or an exception.
        return template
