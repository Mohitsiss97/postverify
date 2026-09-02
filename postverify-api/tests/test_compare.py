"""The comparison engine: recognise the same image, reject a different one.

These tests use the same variants the thresholds were calibrated on: resizing,
recompression, cropping, rotation and watermarking. The images are synthetic but
texture-rich, so ORB finds keypoints in them and they behave like real photos.
"""
import cv2
import numpy as np
import pytest

from app.compare import ImageError, compare, decode, hamming, phash, sha256


def make_image(seed: int, size: int = 640) -> np.ndarray:
    """A deterministic, texture-rich image: shapes over noise."""
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 190, (size, size), dtype=np.uint8)
    for _ in range(40):
        x, y = rng.integers(0, size - 90, 2)
        w, h = rng.integers(25, 90, 2)
        shade = int(rng.integers(0, 255))
        if rng.random() < 0.5:
            cv2.rectangle(img, (x, y), (x + w, y + h), shade, -1)
        else:
            cv2.circle(img, (x + w // 2, y + h // 2), int(w // 2), shade, -1)
    return cv2.GaussianBlur(img, (3, 3), 0)


def jpg(img, quality: int = 92) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return buf.tobytes()


@pytest.fixture(scope="module")
def base():
    return make_image(11)


@pytest.fixture(scope="module")
def base_bytes(base):
    return jpg(base)


# ---------------- recognise the same image ----------------

def test_identical_file(base_bytes):
    r = compare(base_bytes, base_bytes)
    assert r.verdict == "identical" and r.exact and r.present
    assert r.confidence == 1.0


def test_recompressed(base, base_bytes):
    """The same image saved again: a different file, the same picture."""
    r = compare(jpg(base, quality=35), base_bytes)
    assert r.present and not r.exact
    assert r.verdict == "same"


@pytest.mark.parametrize("factor", [0.5, 0.25, 0.12])
def test_resized(base, base_bytes, factor):
    h, w = base.shape
    small = cv2.resize(base, (int(w * factor), int(h * factor)))
    assert compare(jpg(small), base_bytes).present


@pytest.mark.parametrize("keep", [0.6, 0.45, 0.3])
def test_cropped(base, base_bytes, keep):
    """pHash fails under cropping; this is where ORB does the work."""
    h, w = base.shape
    m = (1 - keep) / 2
    crop = base[int(h * m):int(h * (1 - m)), int(w * m):int(w * (1 - m))]
    r = compare(jpg(crop), base_bytes)
    assert r.present, f"keep={keep} was missed: {r._debug}"
    assert r.phash_distance > 8, "pHash was expected to fail on a crop"
    assert r.orb_inliers >= 12


def test_rotated(base, base_bytes):
    h, w = base.shape
    rot = cv2.warpAffine(base, cv2.getRotationMatrix2D((w / 2, h / 2), 12, 1.0), (w, h))
    assert compare(jpg(rot), base_bytes).present


def test_watermarked(base, base_bytes):
    h, w = base.shape
    wm = base.copy()
    cv2.rectangle(wm, (0, int(h * .6)), (w, int(h * .9)), 255, -1)
    assert compare(jpg(wm), base_bytes).present


def test_brightened(base, base_bytes):
    bright = cv2.convertScaleAbs(base, alpha=1.3, beta=30)
    assert compare(jpg(bright), base_bytes).present


def test_screenshot_with_padding(base, base_bytes):
    """A screenshot also captures the interface around the post."""
    h, w = base.shape
    canvas = np.full((int(h * 1.4), w), 28, dtype=np.uint8)
    canvas[int(h * .2):int(h * .2) + h, :] = base
    assert compare(jpg(canvas), base_bytes).present


# ---------------- reject a different image ----------------

@pytest.mark.parametrize("seed", [12, 13, 14, 15, 16])
def test_different_images_rejected(base_bytes, seed):
    r = compare(jpg(make_image(seed)), base_bytes)
    assert not r.present, f"seed={seed} matched by mistake: {r._debug}"
    assert r.verdict == "different"


def test_blank_image_rejected(base_bytes):
    blank = np.full((400, 400), 200, dtype=np.uint8)
    assert not compare(jpg(blank), base_bytes).present


def test_flip_is_not_the_same_image(base, base_bytes):
    """A mirrored image is a different image and must not count as the same."""
    assert not compare(jpg(cv2.flip(base, 1)), base_bytes).present


# ---------------- building blocks ----------------

def test_phash_stable_across_resize(base):
    h, w = base.shape
    small = cv2.resize(base, (w // 3, h // 3))
    assert hamming(phash(base), phash(small)) <= 8


def test_phash_differs_for_different_images():
    assert hamming(phash(make_image(1)), phash(make_image(2))) > 8


def test_sha256_changes_with_one_byte(base_bytes):
    assert sha256(base_bytes) != sha256(base_bytes + b"\x00")


def test_decode_rejects_junk():
    with pytest.raises(ImageError):
        decode(b"this is not an image")


def test_decode_rejects_empty():
    with pytest.raises(ImageError):
        decode(b"")


def test_coverage_hints_at_crop(base, base_bytes):
    h, w = base.shape
    crop = base[int(h * .25):int(h * .75), int(w * .25):int(w * .75)]
    r = compare(jpg(crop), base_bytes)
    assert r.present and r.coverage is not None
    assert r.coverage < 0.8, "a crop should cover well under the whole image"


# ---------------- percentage score ----------------

def test_identical_scores_100(base_bytes):
    assert compare(base_bytes, base_bytes).score == 100


def test_score_high_for_real_matches(base, base_bytes):
    """Every genuine match should score above 70 (calibration minimum: 74)."""
    h, w = base.shape
    variants = {
        "resize": cv2.resize(base, (w // 3, h // 3)),
        "crop": base[int(h * .2):int(h * .8), int(w * .2):int(w * .8)],
        "rotate": cv2.warpAffine(
            base, cv2.getRotationMatrix2D((w / 2, h / 2), 10, 1.0), (w, h)),
    }
    for name, img in variants.items():
        s = compare(jpg(img), base_bytes).score
        assert s >= 70, f"{name} only scored {s}"


def test_score_low_for_different_images(base_bytes):
    """Different images should stay below 40 (calibration maximum: 25)."""
    for seed in (41, 42, 43, 44):
        s = compare(jpg(make_image(seed)), base_bytes).score
        assert s < 40, f"seed={seed} scored {s}, which is far too high"


def test_score_never_out_of_range(base, base_bytes):
    for data in (base_bytes, jpg(make_image(77)), jpg(cv2.resize(base, (80, 80)))):
        assert 0 <= compare(data, base_bytes).score <= 100
