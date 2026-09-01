# PostVerify API

Teen endpoints. Koi web page nahi, koi session nahi, koi temp file nahi.

| | |
|---|---|
| `POST /v1/time` | Post kab upload hua |
| `POST /v1/within` | Post 1d / 3d / 7d / 15d / 1m ke andar ka hai ya nahi |
| `POST /v1/verify` | Di hui image us post me hai ya nahi, kitne % match |

Platform chunna nahi padta — URL se khud pehchana jata hai: **X, Instagram,
Facebook, LinkedIn, YouTube**. Koi login, koi API key nahi.

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000    # docs: /docs
pytest -q                            # 147 tests
```

Docker: `docker build -t postverify-api . && docker run -p 8000:8000 postverify-api`

---

## 1. Upload time

```bash
curl -X POST localhost:8000/v1/time \
  -H "content-type: application/json" \
  -d '{"url":"https://x.com/elonmusk/status/1026872652290379776","tz":"Asia/Kolkata"}'
```

```json
{
  "ok": true,
  "platform": "x",
  "post_id": "1026872652290379776",
  "canonical_url": "https://x.com/elonmusk/status/1026872652290379776",
  "time": {
    "published_at": "2018-08-07T16:48:13.334000Z",
    "published_at_local": "2018-08-07T22:18:13.334000+05:30",
    "timezone": "Asia/Kolkata",
    "age_seconds": 255074000,
    "age_human": "8 saal purana",
    "method": "id-embedded",
    "precision": "millisecond"
  }
}
```

`GET /v1/time?url=...&tz=...` bhi chalta hai — browser me paste karke dekh sakte ho.

## 2. Window check

```bash
curl -X POST localhost:8000/v1/within \
  -H "content-type: application/json" \
  -d '{"url":"https://www.instagram.com/p/DceLPdrCR3L/","within":"1d,3d,7d,15d,1m"}'
```

```json
{
  "time": {"published_at": "2026-08-25T17:29:13Z", "age_human": "7 din purana"},
  "within": {"1d": false, "3d": false, "7d": true, "15d": true, "1m": true},
  "within_detail": {
    "7d": {"seconds": 604800, "cutoff": "2026-08-25T08:35:11Z", "within": true}
  },
  "checked_at": "2026-09-01T08:35:11Z"
}
```

Ek hi window bhejo to seedha boolean bhi milta hai — nested object khodna na pade:

```json
{"within": {"7d": true}, "is_within": true}
```

### Units

| Likhiye | Matlab |
|---|---|
| `s` `sec` | second |
| `min` | minute |
| `h` `hr` | ghanta |
| `d` `day` | din — unit na likho to yahi (`7` = `7d`) |
| `w` `week` | hafta |
| `m` `mo` `month` | **month = 30 din** |
| `y` `year` | saal = 365 din |

> **`m` ka matlab month hai, minute nahi.** Zyadatar date libraries me `m` = minute
> hota hai; yahan jaan-boojh kar alag rakha hai. Minute ke liye `min` likhiye —
> `1min` ek minute, `1m` ek mahina.

## 3. Image verify

Multipart form (file jaati hai, isliye JSON nahi):

```bash
curl -X POST localhost:8000/v1/verify \
  -F "url=https://www.instagram.com/p/DceLPdrCR3L/" \
  -F "image=@meri.jpg"

# file ki jagah image ka URL — server-to-server ke liye aasan
curl -X POST localhost:8000/v1/verify \
  -F "url=https://www.instagram.com/p/DceLPdrCR3L/" \
  -F "image_url=https://example.com/meri.jpg"
```

```json
{
  "time": {"published_at": "2026-08-25T17:29:13Z"},
  "image": {
    "checked": true,
    "present": true,
    "verdict": "identical",
    "score": 100,
    "images_checked": 1,
    "matched": {"tier": "post", "score": 100, "orb_inliers": 0, "phash_distance": 0}
  }
}
```

Image na mile to `"matched"` hota hi nahi:

```json
{"image": {"present": false, "verdict": "different", "score": 0}}
```

`/v1/verify` optional `within` bhi leta hai. Ye sirf suvidha ke liye nahi hai —
Instagram/Facebook pe har call ek browser render hai, to `within` alag call me
maangne se **dobara render** hota. Ek saath maang lene se ek hi render me kaam ho
jata hai.

```bash
curl -X POST localhost:8000/v1/verify \
  -F "url=https://www.instagram.com/p/DceLPdrCR3L/" \
  -F "image=@meri.jpg" -F "within=7d"
```

---

## Response ke fields

| Field | Matlab |
|---|---|
| `time.method` | `id-embedded` — timestamp URL ke andar hi tha, koi network call nahi (X, LinkedIn)<br>`public-page` — public page se padha (YouTube)<br>`headless-page` — browser me render karke (Instagram, Facebook) |
| `time.precision` | `millisecond` (X, LinkedIn) ya `second` |
| `image.verdict` | `identical` bilkul wahi file · `same` wahi image (resize/crop/watermark ke baad bhi) · `likely` shayad · `different` nahi mili |
| `image.score` | 0–100. Asli match **74 se upar**, alag image **25 se neeche** |
| `image.present` | Upar ke teen achhe verdicts pe `true`. **Faisla isse lijiye, score se nahi** — score dikhane ke liye hai, verdict calibrated thresholds pe chalta hai |
| `image.matched.tier` | `post` = pakka isi post ki image · `page` = post ke page pe mili (carousel slide ya related post ho sakti hai) |

## Adhoora jawab bhi jawab hai

Time aur image alag-alag chalte hain — ek fail ho to doosra rukta nahi.

- Post me image na ho → `time` phir bhi aata hai, `image.checked: false` ke saath
- Time na mile → image match phir bhi chalta hai, `time_error` alag se aata hai
- **Time na mile to `within` ka jawab `null` aata hai, `false` nahi** — `false`
  padha jaata "post purana hai" ki tarah, jo jhooth hota

Dono fail hon tabhi request error banti hai, aur tab **asli error** wapas jata hai
(`invalid_id`, `not_visible`, …) — generic 502 me nahi badalta.

## Errors

Sab errors ek hi shape me, to parsing ek jagah likhni padegi:

```json
{"detail": {"error": "unsupported_url", "message": "..."}}
```

| HTTP | error | Kab |
|---|---|---|
| 400 | `unsupported_url` | URL kisi supported platform ka nahi |
| 400 | `bad_window` | `within` samajh nahi aaya |
| 400 | `bad_image` | Image decode nahi hui, ya di hi nahi |
| 401 | `unauthorized` | `ACCESS_TOKEN` set hai aur token galat/nadaarad |
| 404 | `no_media` | Post me koi image hai hi nahi (text-only post) |
| 404 | `not_visible` | Post private hai ya login maang raha hai |
| 413 | `too_large` | Image 25 MB se badi |
| 422 | `invalid_id` | ID mila par timestamp plausible nahi (2010 se pehle ka tweet) |
| 502 | `upstream_error` | Page render ya download fail |
| 503 | `not_configured` | Browser chahiye par mila nahi |

Do cheezein galat jawab se bachati hain:

- **`within` pehle parse hoti hai, kaam baad me.** Galat window pe `400` turant milta
  hai — Instagram ka 15 second ka render shuru hi nahi hota.
- **Kharab upload pehle check hota hai.** Post ki images tab download hoti hain jab
  aapki image decode ho chuki ho.

## Kitna time lagta hai

| Platform | `/v1/time`, `/v1/within` | `/v1/verify` |
|---|---|---|
| X | ~0s — ID ke andar se, koi network call nahi | ~3–8s |
| LinkedIn | ~0s | ~8s |
| YouTube | ~3s | ~6s |
| Instagram | ~13s | ~15s |
| Facebook | ~6–12s | ~12s |

Instagram aur Facebook pe browser chalta hai — client ka timeout **kam se kam
60 second** rakhiye.

Browser waale platforms CPU-bound hain: concurrency se throughput badhta **nahi**,
`HEADLESS_MAX_CONCURRENT` sirf memory ka cap hai. Ek machine pe ~9–10 render/minute.

## Data

Kuch bhi disk pe nahi likha jata. Post ki images sirf request ki memory me aati
hain aur wahin khatam ho jaati hain; aapki bheji hui image bhi. Isliye mitane ko
kuch bachta hi nahi.

## Config

| Var | Default | Kaam |
|---|---|---|
| `PLATFORMS` | sab | Kaunse platforms chalein — `x,linkedin,youtube` |
| `ACCESS_TOKEN` | khali | Set ho to token ke bina kuch nahi chalega |
| `CHROME_PATH` | auto | Chrome/Edge ka path |
| `HEADLESS_MAX_CONCURRENT` | 4 | Ek waqt me kitne browser |
| `HEADLESS_TIMEOUT_SEC` | 45 | Ek page pe max intezaar |
| `PORT` | 8000 | Host inject karta hai |
| `YOUTUBE_API_KEY` | khali | Optional — YouTube ka time API se (page fallback rehta hai) |

### Token

`ACCESS_TOKEN` set karte hi teeno endpoints bina token ke `401` denge. Teen tareeke:

```bash
-H "X-Access-Token: aapka-token"          # header
-d '{"url":"...", "token":"aapka-token"}'  # JSON body
?token=aapka-token                         # query (GET pe)
```

**Public deploy pe ye zaroor lagayein.** Bina iske koi bhi aapke server se
Instagram/Facebook hit kar sakta hai, aur block **aapke IP** pe aayega.

## Code map

```
app/main.py           teen endpoints + health/platforms
app/service.py        detect -> ek render -> time + images -> best match
app/window.py         "1d, 7d, 1m" parse aur check
app/compare.py        SHA-256 + pHash + ORB/RANSAC, calibrated
app/platforms/*.py    ek file = ek platform (URL match, time, images)
app/fetch.py          HTTP: page laana, image download (25 MB cap)
app/browser.py        headless Chrome (concurrency capped)
app/http.py           token guard, error mapping, upload padhna
```

Naya platform: `app/platforms/<naam>.py` banao (`match`, `load`, `published_at`,
`images`) aur `app/platforms/__init__.py` ke `CATALOG` me daal do. Baaki sab apne
aap chalne lagta hai.
