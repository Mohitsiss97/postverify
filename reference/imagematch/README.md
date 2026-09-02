# ImageMatch

Ek image do aur ek post ka URL do → batata hai ki **wo image us post me hai ya nahi**.

Size, compression, crop, watermark, rotation — in sab se farq nahi padta. Sawaal
sirf ek hai: ye image us post pe maujood hai ya nahi.

Ye [PostTime](../posttime/) se **alag service** hai. Wahan ka code chhua nahi gaya;
sirf `browser.py` copy kiya gaya hai taaki dono ek doosre pe depend na karein.

## Chalane ke liye

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001    # http://localhost:8001
pytest -q                                    # 67 tests
```

Docker: `docker build -t imagematch . && docker run -p 8001:8001 imagematch`

## Per-platform services

| Route | Platform | Image kahan se | Browser chahiye |
|---|---|---|---|
| `POST /x/match` | X (Twitter) | og:image (profile pic filter karke) | nahi |
| `POST /youtube/match` | YouTube | video ka thumbnail | nahi |
| `POST /instagram/match` | Instagram | rendered DOM ki CDN images | haan |
| `POST /facebook/match` | Facebook | rendered DOM ki CDN images | haan |
| `POST /linkedin/match` | LinkedIn | rendered DOM ki licdn images | haan |

```bash
curl -X POST localhost:8001/youtube/match \
  -F "url=https://www.youtube.com/watch?v=jNQXAC9IVRw" \
  -F "image=@meri-image.jpg"
```

```json
{
  "present": true,
  "verdict": "same",
  "platform": "youtube",
  "post_id": "jNQXAC9IVRw",
  "summary": "Haan — wahi image hai (post ki apni image). Wahi image hai — aapki image post waali ka crop lagti hai",
  "images_checked": 1,
  "matched": {
    "url": "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg",
    "tier": "post",
    "verdict": "same",
    "confidence": 0.95,
    "phash_distance": 24,
    "orb_inliers": 318
  }
}
```

Shared: `GET /` (UI) · `GET /platforms` · `POST /match` (auto-detect) · `GET /health`

## Compare kaise hota hai

Teen level, sasta se mehenga. Pehla jo jawab de de, wahi:

| Level | Kya pakadta hai | Kab fail hota hai |
|---|---|---|
| **SHA-256** | bilkul wahi file | thoda sa bhi re-save hone pe |
| **pHash** | resize, compress, brightness | crop hone pe |
| **ORB + RANSAC** | crop, watermark, rotation, screenshot | bahut heavy crop pe |

ORB image me keypoints (corners, texture) dhoondta hai, dono images ke keypoints
match karta hai, aur phir RANSAC se check karta hai ki wo matches **ek hi geometric
transform** follow karte hain ya bas random shor hain.

### Calibration se jo sabse kaam ki baat nikli

7 asli images ke 20 variants pe thresholds calibrate kiye. Jo mila:

**Asli match ko inlier _count_ se nahi, inlier _ratio_ se pehchana jaata hai.**

| | inliers | ratio |
|---|---|---|
| Asli match (mushkil variants tak) | 15 – 1594 | **0.87 – 1.00** |
| Alag images | 5 – 17 | **0.33 – 0.50** |

Count overlap karta hai (17 vs 15), ratio nahi. Wajah saaf hai: jab image sach me
wahi hai to lagbhag saare matches ek hi transform pe agree karte hain. Do alag
images me kuch keypoints ittefaqan match ho jaate hain, par wo kisi ek transform
pe agree nahi karte. Isliye `_MIN_INLIER_RATIO = 0.65` — dono ke beech ki khaayi me.

Pehle maine threshold count pe rakha tha aur ek false positive aa raha tha. Ratio
pe shift karte hi wo chala gaya, aur ek bhi true positive nahi toota.

### Kahan tak chalta hai

Calibration ka nateeja (7 images x har variant):

| Variant | Pakda |
|---|---|
| Resize (25% tak), thumbnail 160px | 7/7 |
| JPEG quality 15 tak | 7/7 |
| Crop — image ka 30% (area ka 9%) | 7/7 |
| Rotation 15° aur 90° | 7/7 |
| Watermark jo 40% dhak de | 7/7 |
| Screen ka photo (perspective + blur + noise) | 7/7 |
| **Crop — image ka 20% (area ka 4%)** | **4/7 — yahi limit hai** |
| **Alag images (21 jode)** | **0 false positives** |

Bahut chhota crop miss ho sakta hai. Mirror/flip ki hui image ko "wahi" nahi maana
jaata — wo jaan-boojh kar hai, kyunki flip ek alag image hai.

## "post" tier aur "page" tier

Post ke page pe sirf us post ki images nahi hoti — carousel ki doosri slides bhi
hoti hain, aur "more posts" section ki doosri posts ki bhi. Regex se ye farq
pakka nahi kiya ja sakta.

Isliye service jhooth nahi bolti, do tier me batati hai:

- **`post`** — og:image se aayi, yaani pakka isi post ki hai
- **`page`** — post ke page pe mili; carousel ki slide ho sakti hai, ya related post ki

Result me `matched.tier` batata hai match kahan hua, aur UI bhi saaf likhta hai.

## Platform-specific baatein

**X** — text-only tweet pe og:image me author ki **profile picture** aati hai, post ki
media nahi. Service usse filter karti hai (`/profile_images/`) aur `no_media` deti
hai, warna har text tweet pe hum avatar compare karte rehte.

**Instagram** — CDN URL ka prefix batata hai image ka type: `t51.82787-15` post ki
media hai, `t51.82787-19` aur `t51.2885-19` profile pictures. Sirf `-15` li jaati hai.
Ek hi image kai sizes me aati hai (640, 1080...) — file id pe group karke sabse bada
version liya jaata hai, kyunki chhoti image se compare karna kamzor hota hai.

**YouTube** — video ki andar ki frames nahi dekhi jaatin, sirf **thumbnail**. Frames
ke liye poora video download karna padta, jo bahut mehenga hai. `maxresdefault` har
video pe nahi hota, isliye bade se chhote ki taraf try hota hai.

**LinkedIn** — plain HTTP pe og:image me favicon aata hai (test karke dekha), isliye
browser zaroori hai. `company-logo` aur `profile-displayphoto` filter kiye jaate hain.

## Kitna time lagta hai

| Platform | Time |
|---|---|
| X, YouTube | ~1-4s (download + compare) |
| Instagram, Facebook, LinkedIn | ~11-17s (browser render + download + compare) |

Comparison khud tez hai (~100ms per image). Waqt page render aur image download me
jaata hai. Browser waale platforms pe wahi concurrency limit lagti hai jo PostTime
me hai — `HEADLESS_MAX_CONCURRENT` (default 4), aur throughput CPU-bound hai.

## Error contract

| HTTP | error | matlab |
|---|---|---|
| 400 | `unsupported_url` | URL us platform ka valid post link nahi |
| 400 | `wrong_platform` | Link kisi aur platform ka hai |
| 400 | `bad_image` | Upload ki hui image decode nahi hui |
| 404 | `no_media` | Post me koi image hai hi nahi |
| 404 | `not_visible` | Post private hai ya login maang raha hai |
| 413 | `too_large` | Image 25 MB se badi |
| 502 | `upstream_error` | Page render ya image download fail |
| 503 | `not_configured` | Browser chahiye par mila nahi |

## Code map

```
app/compare.py        SHA-256 + pHash + ORB/RANSAC, calibrated thresholds
app/media/base.py     Source contract + errors + ImageRef (tier, group)
app/media/*.py        ek file = ek platform ka image extractor
app/media/__init__.py registry: CATALOG, enabled(), detect()
app/fetch.py          HTTP: page laana, image download (size cap ke saath)
app/browser.py        headless Chrome (PostTime se copy, concurrency capped)
app/service.py        orchestration: URL -> images -> compare -> best match
app/main.py           per-platform routers + shared endpoints
app/web/index.html    UI (platform picker -> URL -> image upload -> result)
```

## Naya platform add karna

1. `app/media/<naam>.py` — `Source` extend karo, `match()` aur `images()` likho.
2. `app/media/__init__.py` ke `CATALOG` me daalo.

Route, `/platforms`, aur UI ka picker apne aap update ho jaate hain.

## Aage kya ho sakta hai

- Video ki frames sample karke compare (abhi sirf thumbnail)
- Carousel slides ko "page" se "post" tier me pakka karna (DOM structure se)
- Bahut chhote crops ke liye SIFT (ORB se dheema, par zyada tolerant)
