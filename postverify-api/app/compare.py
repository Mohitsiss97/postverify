"""Comparing two images: is this the same image?

The question is not "are these two files identical" but "is this image present
in that post". Size, compression, cropping and watermarking must not change the
answer. So the comparison runs at three levels, cheapest first:

    1. SHA-256      byte-for-byte the same file? (instant, certain)
    2. pHash        visually the same? (survives resizing and recompression)
    3. ORB + RANSAC still the same after cropping, watermarking or rotation?

The third level does the real work. ORB finds keypoints (corners, texture),
matches them, and RANSAC then checks whether those matches all follow a single
geometric transform or are merely noise. That check is what prevents false
positives: even two completely unrelated images share a few coincidental
keypoint matches, but those matches never agree on one transform.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import cv2
import numpy as np

# Large images are scaled down. ORB does not need that much detail, and the
# speed-up is several-fold.
_WORK_SIZE = 900
_PHASH_SIZE = 32
_ORB_FEATURES = 3000
_LOWE_RATIO = 0.75

# These thresholds were calibrated on 20 variants each of 7 real images.
#
# The most valuable finding from that calibration: a genuine match is
# identified by the inlier *ratio*, not the inlier *count*. When the image
# really is the same, nearly every good match follows one homography (ratio
# 0.87-1.00). Two different images do produce a few coincidental keypoint
# matches, but those never agree on a single transform (ratio 0.33-0.50).
# There is a clean gap between the two populations.
_PHASH_SAME = 8            # below this, visually the same image (different: 28+)
_MIN_INLIER_RATIO = 0.65   # genuine match: 0.87+, noise: below 0.50
_INLIERS_STRONG = 25       # this many consistent matches, plus ratio, is conclusive
_INLIERS_WEAK = 12         # this many is suggestive; guards against small-sample flukes


class ImageError(ValueError):
    """The image could not be decoded."""


@dataclass
class Comparison:
    verdict: str                    # identical | same | likely | different
    score: int                      # 0-100 similarity indicator
    confidence: float               # 0..1
    exact: bool
    phash_distance: int | None
    orb_inliers: int
    orb_good_matches: int
    coverage: float | None          # how much of the post image the upload covers
    note: str = ""
    _debug: dict = field(default_factory=dict, repr=False)

    @property
    def present(self) -> bool:
        """Whether the image is present in the post — the question that matters."""
        return self.verdict in ("identical", "same", "likely")


# --- decoding -----------------------------------------------------------

def decode(data: bytes) -> np.ndarray:
    """Decode bytes to a grayscale image, in any format OpenCV can read."""
    if not data:
        raise ImageError("The image is empty")
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ImageError(
            "This image could not be decoded — the format is unsupported or the "
            "file is corrupt")
    return img


def _fit(img: np.ndarray, size: int = _WORK_SIZE) -> np.ndarray:
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= size:
        return img
    scale = size / longest
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


# --- level 1: exact -----------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- level 2: perceptual hash -------------------------------------------

def phash(img: np.ndarray) -> int:
    """DCT-based perceptual hash; unchanged by resizing and recompression."""
    small = cv2.resize(img, (_PHASH_SIZE, _PHASH_SIZE), interpolation=cv2.INTER_AREA)
    freq = cv2.dct(np.float32(small))
    block = freq[:8, :8].flatten()
    # Skip the DC component (the very first value); it only carries overall
    # brightness.
    median = np.median(block[1:])
    bits = block > median
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# --- level 3: feature matching ------------------------------------------

def _orb_match(a: np.ndarray, b: np.ndarray) -> tuple[int, int, float | None]:
    """Return (inliers, good_matches, coverage), where coverage is a's share of b."""
    orb = cv2.ORB_create(nfeatures=_ORB_FEATURES)
    kp_a, des_a = orb.detectAndCompute(a, None)
    kp_b, des_b = orb.detectAndCompute(b, None)
    if des_a is None or des_b is None or len(des_a) < 2 or len(des_b) < 2:
        return 0, 0, None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(des_a, des_b, k=2)

    # Lowe's ratio test: the best match must be clearly better than the second
    # best, otherwise it is not trustworthy.
    good = [m for m, n in (p for p in pairs if len(p) == 2)
            if m.distance < _LOWE_RATIO * n.distance]
    if len(good) < 8:
        return 0, len(good), None

    src = np.float32([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if matrix is None or mask is None:
        return 0, len(good), None

    inliers = int(mask.sum())

    # Reject a degenerate homography: if every point collapsed onto one line the
    # inlier count looks high but means nothing.
    if abs(np.linalg.det(matrix[:2, :2])) < 1e-6:
        return 0, len(good), None

    coverage = _coverage(a.shape, b.shape, matrix)
    return inliers, len(good), coverage


def _coverage(shape_a: tuple, shape_b: tuple, matrix: np.ndarray) -> float | None:
    """Project image a onto b and report how much of b it covers."""
    h, w = shape_a[:2]
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    try:
        projected = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2)
    except cv2.error:
        return None
    area = 0.5 * abs(
        sum(projected[i][0] * projected[(i + 1) % 4][1] -
            projected[(i + 1) % 4][0] * projected[i][1] for i in range(4)))
    target = shape_b[0] * shape_b[1]
    return round(min(area / target, 4.0), 3) if target else None


def similarity_score(distance: int, inliers: int, good: int, ratio: float) -> int:
    """A 0-100 similarity indicator, intended for display.

    Two routes are computed and the higher one wins:

      From pHash : how closely the whole image matches. Distance 0 gives 100,
                   32 or more gives 0. This collapses under cropping even when
                   the image is the same.
      From ORB   : how closely the geometry matches. This only counts when the
                   matches agree on a single transform (above the ratio
                   threshold); otherwise it is 0. Once confirmed it starts at
                   55, because geometric confirmation is strong evidence in
                   its own right.

    The score is an *indicator*, not the decision. The verdict is the decision.
    """
    from_phash = max(0.0, 1 - distance / 32)

    from_orb = 0.0
    if good and ratio >= _MIN_INLIER_RATIO:
        strength = min(inliers / 60, 1.0)                                  # how many
        quality = min((ratio - _MIN_INLIER_RATIO) / (1 - _MIN_INLIER_RATIO), 1.0)
        from_orb = 0.55 + 0.45 * (0.6 * strength + 0.4 * quality)

    return int(round(100 * max(from_phash, from_orb)))


# --- the three levels combined ------------------------------------------

def compare(uploaded: bytes, candidate: bytes) -> Comparison:
    """Compare an uploaded image against one image from the post."""
    if sha256(uploaded) == sha256(candidate):
        return Comparison("identical", 100, 1.0, True, 0, 0, 0, 1.0,
                          "Byte-for-byte the same file")

    img_a = _fit(decode(uploaded))
    img_b = _fit(decode(candidate))

    distance = hamming(phash(img_a), phash(img_b))
    inliers, good, coverage = _orb_match(img_a, img_b)
    ratio = (inliers / good) if good else 0.0

    score = similarity_score(distance, inliers, good, ratio)
    debug = {"phash": distance, "inliers": inliers, "good": good,
             "ratio": round(ratio, 3), "coverage": coverage, "score": score}

    # pHash looks at the whole image, so it fails under cropping — but when it
    # does say yes, it is highly reliable.
    if distance <= _PHASH_SAME:
        return Comparison("same", score, 0.97, False, distance, inliers, good, coverage,
                          "The same image, only resized or recompressed", debug)

    if inliers >= _INLIERS_STRONG and ratio >= _MIN_INLIER_RATIO:
        return Comparison("same", score, min(0.95, 0.6 + inliers / 200), False, distance,
                          inliers, good, coverage, _shape_note(coverage), debug)

    if inliers >= _INLIERS_WEAK and ratio >= _MIN_INLIER_RATIO:
        return Comparison("likely", score, 0.55 + inliers / 200, False, distance,
                          inliers, good, coverage,
                          "A substantial part matches, but not conclusively — "
                          "review manually", debug)

    return Comparison("different", score, 0.0, False, distance, inliers, good, coverage,
                      "This image was not found in the post", debug)


def _shape_note(coverage: float | None) -> str:
    if coverage is None:
        return "The same image"
    if coverage < 0.55:
        return "The same image — the upload appears to be a crop of the post image"
    if coverage > 1.8:
        return "The same image — the post image appears to be a crop of the upload"
    return "The same image — possibly edited or recompressed"
