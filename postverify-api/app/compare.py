"""Do images compare karna: kya ye wahi image hai?

Sawaal ye nahi hai ki "dono files same hain kya" — sawaal ye hai ki "kya ye image
us post me maujood hai". Size, compression, crop, watermark — in sab se farq nahi
padna chahiye. Isliye teen level pe dekha jaata hai, sasta se mehenga:

    1. SHA-256      bilkul wahi file? (turant, 100% pakka)
    2. pHash        dikhne me wahi? (resize/compress ke baad bhi same rehta hai)
    3. ORB + RANSAC crop/watermark/rotate ke baad bhi wahi? (asli kaam yahan hota hai)

ORB keypoints dhoondta hai (corners, texture), unhe match karta hai, aur phir
RANSAC se check karta hai ki matches ek hi geometric transform follow karte hain
ya bas random shor hain. Yahi random matches se bachata hai — do bilkul alag
images me bhi kuch keypoints ittefaqan match ho jaate hain, par wo ek consistent
transform nahi banate.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import cv2
import numpy as np

# Bade images ko chhota kar dete hain — ORB ko itni detail chahiye hi nahi,
# aur speed kai guna badh jaati hai.
_WORK_SIZE = 900
_PHASH_SIZE = 32
_ORB_FEATURES = 3000
_LOWE_RATIO = 0.75

# Ye thresholds 7 asli images ke 20 variants pe calibrate kiye gaye hain.
#
# Sabse kaam ki cheez jo calibration me nikli: asli match ko inlier *count* se
# nahi, inlier *ratio* se pehchana jaata hai. Jab image sach me wahi hai to
# lagbhag saare good matches ek hi homography follow karte hain (ratio 0.87-1.00).
# Do alag images me kuch keypoints ittefaqan match ho jaate hain, par wo kisi
# ek transform pe agree nahi karte (ratio 0.33-0.50). Beech me saaf khaayi hai.
_PHASH_SAME = 8            # isse kam = dikhne me wahi image (alag images: 28+)
_MIN_INLIER_RATIO = 0.65   # asli match: 0.87+, shor: 0.50 se neeche
_INLIERS_STRONG = 25       # itne consistent matches + ratio = pakka wahi image
_INLIERS_WEAK = 12         # itne = shayad wahi (chhote sample ke fluke se bachne ko)


class ImageError(ValueError):
    """Image decode hi nahi hui."""


@dataclass
class Comparison:
    verdict: str                    # identical | same | likely | different
    score: int                      # 0-100, kitna milta hai
    confidence: float               # 0..1
    exact: bool
    phash_distance: int | None
    orb_inliers: int
    orb_good_matches: int
    coverage: float | None          # uploaded image post-image ka kitna hissa hai
    note: str = ""
    _debug: dict = field(default_factory=dict, repr=False)

    @property
    def present(self) -> bool:
        """Kya ye image post me hai — yehi asli jawab hai."""
        return self.verdict in ("identical", "same", "likely")


# --- decoding -----------------------------------------------------------

def decode(data: bytes) -> np.ndarray:
    """Bytes se grayscale image. Koi bhi format jo OpenCV padh sakta hai."""
    if not data:
        raise ImageError("Image khali hai")
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ImageError("Ye image decode nahi hui — format support nahi ya file kharab hai")
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
    """DCT-based perceptual hash. Resize/compress se nahi badalta."""
    small = cv2.resize(img, (_PHASH_SIZE, _PHASH_SIZE), interpolation=cv2.INTER_AREA)
    freq = cv2.dct(np.float32(small))
    block = freq[:8, :8].flatten()
    # DC component (bilkul pehla) chhod do — wo sirf overall brightness hai
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
    """(inliers, good_matches, coverage) — coverage matlab a, b ka kitna hissa hai."""
    orb = cv2.ORB_create(nfeatures=_ORB_FEATURES)
    kp_a, des_a = orb.detectAndCompute(a, None)
    kp_b, des_b = orb.detectAndCompute(b, None)
    if des_a is None or des_b is None or len(des_a) < 2 or len(des_b) < 2:
        return 0, 0, None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(des_a, des_b, k=2)

    # Lowe ratio test — best match doosre se saaf behtar hona chahiye,
    # warna wo match bharosemand nahi hai.
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

    # Homography degenerate to nahi? (sab points ek line pe aa gaye ho to
    # inliers zyada dikhenge par matlab kuch nahi)
    if abs(np.linalg.det(matrix[:2, :2])) < 1e-6:
        return 0, len(good), None

    coverage = _coverage(a.shape, b.shape, matrix)
    return inliers, len(good), coverage


def _coverage(shape_a: tuple, shape_b: tuple, matrix: np.ndarray) -> float | None:
    """Image a ko b pe project karo — b ka kitna hissa cover hota hai."""
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
    """0-100 ka score — user ko dikhane ke liye.

    Do raaste hain aur jo zyada bole wahi liya jaata hai:

      pHash se  : poori image kitni milti hai. distance 0 = 100, 32+ = 0.
                  Crop pe ye gir jaata hai chahe image wahi ho.
      ORB se    : geometry kitni milti hai. Ye tabhi ginta hai jab matches ek hi
                  transform pe agree karein (ratio threshold ke upar) — warna 0.
                  Confirm ho jaye to 55 se shuru hota hai, kyunki geometric
                  confirmation apne aap me majboot sabooot hai.

    Score ek *indicator* hai, faisla nahi. Faisla verdict karta hai.
    """
    from_phash = max(0.0, 1 - distance / 32)

    from_orb = 0.0
    if good and ratio >= _MIN_INLIER_RATIO:
        strength = min(inliers / 60, 1.0)                                  # kitne matches
        quality = min((ratio - _MIN_INLIER_RATIO) / (1 - _MIN_INLIER_RATIO), 1.0)
        from_orb = 0.55 + 0.45 * (0.6 * strength + 0.4 * quality)

    return int(round(100 * max(from_phash, from_orb)))


# --- sab milakar --------------------------------------------------------

def compare(uploaded: bytes, candidate: bytes) -> Comparison:
    """Uploaded image aur post ki ek image — kya ye wahi hai?"""
    if sha256(uploaded) == sha256(candidate):
        return Comparison("identical", 100, 1.0, True, 0, 0, 0, 1.0,
                          "Bilkul wahi file — ek byte bhi alag nahi")

    img_a = _fit(decode(uploaded))
    img_b = _fit(decode(candidate))

    distance = hamming(phash(img_a), phash(img_b))
    inliers, good, coverage = _orb_match(img_a, img_b)
    ratio = (inliers / good) if good else 0.0

    score = similarity_score(distance, inliers, good, ratio)
    debug = {"phash": distance, "inliers": inliers, "good": good,
             "ratio": round(ratio, 3), "coverage": coverage, "score": score}

    # pHash poori image ko dekhta hai — crop pe fail ho jaata hai, par jab wo
    # haan kehta hai to bahut bharosemand hota hai.
    if distance <= _PHASH_SAME:
        return Comparison("same", score, 0.97, False, distance, inliers, good, coverage,
                          "Wahi image hai — sirf resize ya compress hui hai", debug)

    if inliers >= _INLIERS_STRONG and ratio >= _MIN_INLIER_RATIO:
        return Comparison("same", score, min(0.95, 0.6 + inliers / 200), False, distance,
                          inliers, good, coverage, _shape_note(coverage), debug)

    if inliers >= _INLIERS_WEAK and ratio >= _MIN_INLIER_RATIO:
        return Comparison("likely", score, 0.55 + inliers / 200, False, distance,
                          inliers, good, coverage,
                          "Kaafi hissa milta hai, par pakka nahi — khud dekh lijiye", debug)

    return Comparison("different", score, 0.0, False, distance, inliers, good, coverage,
                      "Ye image is post me nahi mili", debug)


def _shape_note(coverage: float | None) -> str:
    if coverage is None:
        return "Wahi image hai"
    if coverage < 0.55:
        return "Wahi image hai — aapki image post waali ka crop lagti hai"
    if coverage > 1.8:
        return "Wahi image hai — post waali aapki image ka crop lagti hai"
    return "Wahi image hai — edit ya compress hui ho sakti hai"
