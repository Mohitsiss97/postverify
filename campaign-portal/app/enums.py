"""Statuses aur reject reasons.

Reason codes hi wo cheez hain jo user ko dikhti hai, isliye inhe ek jagah rakha
hai — API, DB, aur user-facing message teeno yahin se aate hain. Nayi wajah
add karni ho to sirf yahan.
"""
from __future__ import annotations

from enum import StrEnum


class CampaignStatus(StrEnum):
    DRAFT = "draft"          # ban rahi hai, users ko nahi dikhti
    ACTIVE = "active"        # chalu — enroll aur submit ho sakta hai
    CLOSED = "closed"        # band — naye submissions nahi


class EnrollmentStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"  # ek submission approve ho gaya
    CANCELLED = "cancelled"


class SubmissionStatus(StrEnum):
    PENDING = "pending"      # queue me, worker uthayega
    VERIFYING = "verifying"  # worker abhi kaam kar raha hai
    APPROVED = "approved"    # saare checks pass
    REJECTED = "rejected"    # koi rule fail — final, retry nahi hoga
    ERROR = "error"          # takneeki dikkat — retries khatam ho gaye


class RejectReason(StrEnum):
    """Kyun reject hua. `ERROR_*` waale takneeki hain, baaki business rules."""

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


# User ko yahi dikhega. Saaf bhaasha, ilzaam nahi — aur jahan ho sake wahan
# agla kadam bhi bata do.
REASON_MESSAGES: dict[RejectReason, str] = {
    RejectReason.TOO_OLD:
        "Ye post {age} purani hai. Post {window} ghante ke andar ki honi chahiye — "
        "dobara post karke naya link daaliye.",
    RejectReason.IMAGE_MISMATCH:
        "Post me jo image hai wo campaign waali image se match nahi hui. "
        "Check kijiye ki aapne wahi image post ki hai jo yahan se download ki thi.",
    RejectReason.WRONG_PLATFORM:
        "Aapne {declared} chuna tha par ye link {actual} ka hai. "
        "Sahi platform chunkar dobara submit kijiye.",
    RejectReason.DUPLICATE:
        "Ye post pehle hi submit ho chuki hai. Har post ek hi baar gin'i jaati hai.",
    RejectReason.POST_NOT_FOUND:
        "Post khul nahi rahi — ho sakta hai wo private ho ya delete ho gayi ho. "
        "Post public kijiye aur dobara try kijiye.",
    RejectReason.NO_IMAGE_IN_POST:
        "Is post me koi image nahi mili. Campaign ki image ke saath post kijiye.",
    RejectReason.UNSUPPORTED_URL:
        "Ye link kisi supported platform ka nahi lagta. "
        "Post ka poora URL daaliye (Instagram, Facebook, X, LinkedIn ya YouTube).",
    RejectReason.TIME_NOT_AVAILABLE:
        "Post ka upload time nahi mil paya, isliye {window} ghante waala check "
        "nahi ho saka. Thodi der baad dobara try kijiye.",
    RejectReason.NO_CAMPAIGN_ASSETS:
        "Is campaign me abhi koi image nahi hai. Admin se sampark kijiye.",
    RejectReason.CAMPAIGN_CLOSED:
        "Ye campaign band ho chuki hai, naye submissions nahi liye ja rahe.",
    RejectReason.MANUAL_REJECT:
        "Ye submission review me reject ki gayi hai.",
    RejectReason.ENGINE_UNAVAILABLE:
        "Verification service abhi jawab nahi de rahi. Aapka submission surakshit "
        "hai — thodi der baad dobara check kiya jayega.",
}

# In wajahon pe dobara koshish ka matlab hai (takneeki dikkat), baaki final hain.
RETRYABLE: frozenset[RejectReason] = frozenset({
    RejectReason.ENGINE_UNAVAILABLE,
    RejectReason.TIME_NOT_AVAILABLE,
})


def message_for(reason: RejectReason, **context: object) -> str:
    template = REASON_MESSAGES[reason]
    try:
        return template.format(**context)
    except KeyError:
        # Context adhoora ho to message aadha-adhoora dikhane se behtar hai
        # ki placeholder ke bina hi de do.
        return template
