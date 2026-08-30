# PostVerify

Post ka URL daalo → **kab upload hua** pata chal jayega.
Image bhi do → batayega **wo image us post me hai ya nahi**, aur **kitne % match**.

Platform chunna nahi padta — URL se khud pehchan leta hai.

Ye [PostTime](../posttime/) aur [ImageMatch](../imagematch/) ka final roop hai.
**Wo dono services waise ki waisi hain — unhe chhua nahi gaya.** Yahan unka code
padhkar naye sire se joda gaya hai.

## Chalane ke liye

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload      # http://localhost:8000
pytest -q                          # 94 tests
```

Docker: `docker build -t postverify . && docker run -p 8000:8000 postverify`

## Ek hi endpoint

```bash
# sirf time
curl -X POST localhost:8000/verify \
  -F "url=https://x.com/elonmusk/status/1026872652290379776" \
  -F "tz=Asia/Kolkata"

# time + image match
curl -X POST localhost:8000/verify \
  -F "url=https://www.instagram.com/p/DceLPdrCR3L/" \
  -F "image=@meri-image.jpg"
```

```json
{
  "platform": "instagram",
  "post_id": "DceLPdrCR3L",
  "time": {
    "published_at": "2026-08-25T17:29:13Z",
    "published_at_local": "2026-08-25T22:59:13+05:30",
    "age_human": "4 din purana",
    "method": "headless-page"
  },
  "image_checked": true,
  "present": true,
  "score": 98,
  "verdict": "same",
  "matched": {"tier": "post", "orb_inliers": 843, "phash_distance": 26},
  "images_checked": 1,
  "summary": "Post 2026-08-25 22:59 ko upload hua (4 din purana). Aapki image is post me hai — wahi image (post ki apni image), 98% match"
}
```

Baaki: `GET /` (UI) · `POST /prepare` · `GET /media/...` · `DELETE /session/...` · `GET /platforms` · `GET /health`

## Do kadam: prepare, phir check

Pehle embed iframes se preview dikhane ki koshish ki thi — Instagram, X, Facebook
sab ke official public embed links. Wo test me render bhi hote the, par asli
browsers me block ho jaate hain (ad-blocker, third-party frame blocking, tracking
protection). Isliye wo raasta chhod diya.

Ab post ki images **server pe download hoti hain** aur humare apne origin se serve
hoti hain. Browser ke paas rokne ki koi wajah hi nahi bachti.

```bash
# kadam 1 — mehenga kaam yahi hai (6-15s)
curl -X POST localhost:8000/prepare -F "url=https://www.instagram.com/p/DceLPdrCR3L/"
```

```json
{
  "session": "kZ8x…",
  "time": {"published_at": "2026-08-25T17:29:13Z", "age_human": "4 din purana"},
  "images": [
    {"name": "0.jpg", "url": "/media/kZ8x…/0.jpg", "tier": "post", "bytes": 44003}
  ]
}
```

```bash
# kadam 2 — turant (~2s), kyunki images pehle se maujood hain
curl -X POST localhost:8000/verify -F "session=kZ8x…" -F "image=@meri.jpg"
```

Faayda sirf preview ka nahi: render **ek hi baar** hota hai. Pehle preview aur check
alag-alag render karte, ab check me ek bhi network call nahi jaati.

| | Pehle | Ab |
|---|---|---|
| Instagram preview | dikhta hi nahi tha | 14s, image saaf dikhti hai |
| Uske baad check | 14s (dobara render) | **2.4s** (koi network nahi) |

Ek hi call waala tarika bhi chalta hai — `POST /verify` with `url` + `image`. Aur
sirf time chahiye to `url` bhejo bina image ke; tab images download hoti hi nahi
(X aur LinkedIn pe ek bhi network call nahi hoti).

UI me ye do kadam **ek hi form** me hain: URL aur image saath-saath, ek button.
Time aur post ki original image milte hi dikh jaate hain — image compare ka
intezaar nahi karvaya jaata. Button ka label bhi bata deta hai kya hoga:
image nahi di to "Sirf time nikalo", di to "Check karo". Post pehle se khuli ho
aur aap tab image daalo, to compare seedha chal jaata hai — dobara kholne ki
zaroorat nahi.

## Data ka ant

Ye service kuch bhi jama karke nahi rakhti. Post ki downloaded images ke teen ant hain:

1. **Check poora hote hi** — us session ka folder delete. Response me `cleaned_up: true`.
2. **Chhoot jaye to** — TTL sweep (`PREVIEW_TTL_SEC`, default 15 minute). User URL
   daal ke chala gaya, to bhi data pada nahi rehta.
3. **Service band hote waqt** — poora temp root delete.

UI bhi apni taraf se saaf karta hai: URL badla to purana session `DELETE /session/...`
se hata deta hai, aur tab band hone pe `sendBeacon` se batata hai.

**User ki upload ki hui image kabhi disk pe likhi hi nahi jaati** — wo sirf request
ki memory me rehti hai. `GET /health` me `store` batata hai us waqt kitna data pada hai.

```bash
curl localhost:8000/health
# {"store": {"sessions": 0, "files": 0, "ttl_seconds": 900, ...}}
```

`/media/<token>/<name>` pe do guard hain: token random hai (`secrets.token_urlsafe`),
aur file ka naam regex se validate hota hai — `../..` se folder se bahar nikalne ka
raasta band hai. Response `Cache-Control: no-store` ke saath jaata hai.

## Kya support hai

| Platform | Time kahan se | Image kahan se | Browser |
|---|---|---|---|
| X (Twitter) | status ID ke bits — **offline** | og:image | nahi |
| LinkedIn | activity URN ke bits — **offline** | licdn CDN | images ke liye |
| YouTube | watch page ka uploadDate | video thumbnail | nahi |
| Instagram | rendered DOM ka `<time>` | usi render se CDN images | haan |
| Facebook | rendered JSON ka `creation_time` | usi render se CDN images | haan |

URL forms: `/status/`, `/p/`, `/reel/`, `/posts/`, `/watch?v=`, `youtu.be/`,
`/feed/update/urn:li:activity:`, `fb.watch/`, `permalink.php?story_fbid=`, aur
bare numeric Facebook IDs.

## Ek post pe browser sirf ek baar

PostTime aur ImageMatch alag-alag the, to Instagram ke ek post pe browser **do baar**
chalta — ek baar time ke liye, ek baar images ke liye. Wo 12 second ka fizool kharch tha.

Yahan har platform ka ek `load()` step hai jo mehenga kaam ek baar karta hai, aur
time + images dono usi ek nateeje se nikalte hain:

```
load()  ->  ek render / ek fetch
              |-> published_at()   time
              |-> images()         images
```

X aur LinkedIn ka time waise bhi offline nikal aata hai, to unka `load()` khali hai —
network sirf tab chhua jaata hai jab image di gayi ho.

## Adhoora jawab bhi jawab hai

Post me image na ho to bhi time milna chahiye. Time na nikle to bhi image match
chalna chahiye. Isliye dono alag-alag chalte hain aur ek ka fail hona doosre ko
nahi rokta — response me `time_error` / `image_error` alag se aate hain.

Dono fail ho jayein tabhi request error banti hai, aur tab **asli error** hi wapas
jaata hai (`invalid_id`, `not_visible`, ...) — generic 502 me nahi badalta.

## Score kaise banta hai

0-100 ka number, do raaston me se jo zyada bole:

- **pHash se** — poori image kitni milti hai. distance 0 = 100, 32+ = 0.
  Crop pe gir jaata hai chahe image wahi ho.
- **ORB se** — geometry kitni milti hai. Ye **tabhi** ginta hai jab matches ek hi
  transform pe agree karein; confirm ho jaye to 55 se shuru hota hai, kyunki
  geometric confirmation apne aap me majboot saboot hai.

Asli images pe naapa gaya:

```
ASLI MATCH ka score  : 74 - 100
ALAG IMAGE ka score  : 0 - 25
```

Beech me saaf khaayi hai. **Score ek indicator hai, faisla nahi** — faisla `verdict`
karta hai (`identical` / `same` / `likely` / `different`), aur wo thresholds pe
chalta hai, score pe nahi.

## Comparison ki calibration

7 asli images ke 20 variants pe thresholds tune kiye. Jo sabse kaam ki baat nikli:

**Asli match ko inlier _count_ se nahi, inlier _ratio_ se pehchana jaata hai.**

| | inliers | ratio |
|---|---|---|
| Asli match (mushkil variants tak) | 15 – 1594 | **0.87 – 1.00** |
| Alag images | 5 – 17 | **0.33 – 0.50** |

Count overlap karta hai, ratio nahi. Wajah: jab image sach me wahi hai to lagbhag
saare matches ek hi transform pe agree karte hain; alag images me kuch keypoints
ittefaqan match ho jaate hain par kisi ek transform pe agree nahi karte.

Kahan tak chalta hai:

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

Mirror/flip ki hui image "alag" mani jaati hai — jaan-boojh kar.

## "post" tier aur "page" tier

Post ke page pe sirf us post ki images nahi hoti — carousel ki slides aur
"more posts" ki doosri images bhi. Regex se ye farq pakka nahi ho sakta, isliye
service jhooth nahi bolti:

- **`post`** — og:image se, pakka isi post ki
- **`page`** — page pe mili; carousel slide ho sakti hai ya related post ki

`matched.tier` batata hai match kahan hua, UI bhi saaf likhta hai.

## Kitna time lagta hai

| Platform | Sirf time | prepare (time + images) | check (session se) |
|---|---|---|---|
| X, LinkedIn | ~0s (offline) | ~3-8s | ~2s |
| YouTube | ~3s | ~6s | ~2s |
| Instagram | ~13s | ~14s | ~2.5s |
| Facebook | ~6-12s | ~10-14s | ~2.5s |

Browser waale platforms CPU-bound hain — concurrency se throughput nahi badhta,
`HEADLESS_MAX_CONCURRENT` (default 4) sirf memory ka cap hai. Ek machine pe
~9-10 render per minute.

## Error contract

| HTTP | error | matlab |
|---|---|---|
| 400 | `unsupported_url` | URL kisi supported platform ka nahi |
| 400 | `bad_image` | Upload ki hui image decode nahi hui |
| 404 | `no_media` | Post me koi image hai hi nahi |
| 404 | `not_visible` | Post private hai ya login maang raha hai |
| 413 | `too_large` | Image 25 MB se badi |
| 422 | `invalid_id` | ID mila par timestamp plausible nahi (jaise pre-2010 tweet) |
| 502 | `upstream_error` | Page render ya download fail |
| 503 | `not_configured` | Browser chahiye par mila nahi |

## Code map

```
app/platforms/base.py     Platform contract: match / load / published_at / images
app/platforms/*.py        ek file = ek platform (time + images dono)
app/platforms/__init__.py registry + detect() — yahi auto-detection hai
app/compare.py            SHA-256 + pHash + ORB/RANSAC + score
app/snowflake.py          X aur LinkedIn ka offline timestamp maths
app/fetch.py              HTTP: page laana, image download (size cap)
app/browser.py            headless Chrome (concurrency capped)
app/service.py            prepare() aur check() — dono kadam
app/store.py              temp session store + cleanup
app/main.py               /prepare, /media, /verify, /session + meta
app/web/index.html        UI (URL + optional image, koi picker nahi)
```

## Naya platform add karna

1. `app/platforms/<naam>.py` — `Platform` extend karo: `match()`, `load()`,
   `published_at()`, `images()`.
2. `app/platforms/__init__.py` ke `CATALOG` me daalo.

Auto-detection, `/platforms`, aur UI ke sample links — teeno apne aap update ho jaate hain.

## Deployment

```bash
PLATFORMS=x,youtube uvicorn app.main:app --port 8001   # browser chahiye hi nahi
PLATFORMS=instagram,facebook,linkedin uvicorn app.main:app --port 8002
```

Pehle waale container ko Chrome aur outbound access dono se kaat sakte ho.

## Aage kya ho sakta hai

- Video ki frames sample karke compare (abhi sirf thumbnail)
- Carousel slides ko "page" se "post" tier me pakka karna
- Rate limiting per IP; Instagram/Facebook ke liye proxy pool agar volume badhe
