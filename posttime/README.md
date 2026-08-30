# PostTime

Post ka URL do → pata chal jayega **kab upload hua tha**. User se koi login, koi OAuth,
koi token nahi maanga jaata.

Har platform ki **apni alag service** hai — apna route, apna resolver, apna setup.
User pehle platform chunta hai, phir URL daalta hai.

## Chalane ke liye

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload      # http://localhost:8000  (UI)  ·  /docs (API)
pytest -q                          # 106 tests
```

Docker: `docker build -t posttime . && docker run -p 8000:8000 --env-file .env posttime`

Image me Chromium bhi aata hai (Instagram ke liye). Sirf X/LinkedIn/YouTube chahiye to
Dockerfile ki chromium layer nikal do — image ~400 MB chhoti ho jayegi.

## Per-platform services

| Route | Platform | Timestamp kahan se | Setup |
|---|---|---|---|
| `POST /x/resolve` | X (Twitter) | snowflake ID (offline) | kuch nahi |
| `POST /linkedin/resolve` | LinkedIn | activity URN (offline) | kuch nahi |
| `POST /youtube/resolve` | YouTube | public watch page (key optional) | kuch nahi |
| `POST /instagram/resolve` | Instagram | headless browser (token optional) | Chrome/Edge |
| `POST /facebook/resolve` | Facebook | headless browser (token optional) | Chrome/Edge |

Har platform ka `GET /{platform}/info` bhi hai — wo service kaise kaam karti hai,
kya chahiye, sample URL kya hai.

```bash
curl -X POST localhost:8000/x/resolve \
  -H "content-type: application/json" \
  -d '{"url":"https://x.com/elonmusk/status/1026872652290379776","tz":"Asia/Kolkata"}'
```

```json
{
  "platform": "x",
  "platform_label": "X (Twitter)",
  "post_id": "1026872652290379776",
  "canonical_url": "https://x.com/elonmusk/status/1026872652290379776",
  "published_at": "2018-08-07T16:48:13.334000Z",
  "published_at_local": "2018-08-07T22:18:13.334000+05:30",
  "timezone": "Asia/Kolkata",
  "age_seconds": 254356728,
  "age_human": "8 saal purana",
  "method": "id-embedded",
  "precision": "millisecond"
}
```

### Shared endpoints

| Route | Kya karta hai |
|---|---|
| `GET /` | UI — platform picker + URL box |
| `GET /platforms` | Picker isi se banta hai (hardcode nahi) |
| `POST /resolve` | Platform khud detect karke resolve |
| `POST /resolve/batch` | Max 100 URLs ek saath |
| `GET /health` | Kaunsi services live hain |

## Galat platform pe roka jaata hai

User ne khud platform chuna hai, isliye galti chhupayi nahi jaati. Instagram ki
service pe X ka link daalo to:

```json
{"error": "wrong_platform", "expected": "instagram", "actual": "x",
 "message": "Ye X (Twitter) ka link hai, aapne Instagram chuna hai"}
```

`POST /resolve` (auto-detect) me ye check nahi lagta — wahan user ne kuch chuna hi nahi.

## Offline platforms kaise kaam karte hain

X aur LinkedIn dono **snowflake IDs** use karte hain — 64-bit integer jiske upper
41 bits millisecond timestamp hote hain:

```
[ 41 bits timestamp_ms ][ 22 bits machine + sequence ]
```

Matlab timestamp URL ke andar hi baitha hai. Koi network call nahi, koi rate limit
nahi, koi quota nahi — sirf `id >> 22`. Farq sirf epoch ka:

- X: custom epoch `1288834974657` ms (4 Nov 2010)
- LinkedIn: plain Unix epoch

Nov 2010 se pehle ke tweets (ID < 29700859247) snowflake se pehle ke hain — unme
timestamp hai hi nahi, service unhe `422 invalid_id` deti hai.

## YouTube ko key kyun nahi chahiye

Public watch page pe upload time poora baitha hota hai — wahi jo aap bina login ke
browser me dekhte ho:

```html
<meta itemprop="uploadDate" content="2009-10-24T23:57:33-07:00">
```

Seconds aur timezone offset ke saath. Isliye default me service yahi padhti hai —
koi key nahi, koi quota nahi. (`oembed` endpoint se ye nahi milta — usme sirf title,
author aur thumbnail hai, date nahi.)

Key **optional** hai. `YOUTUBE_API_KEY` set karoge to Data API v3 pehle try hogi,
aur page fallback ban jayega:

```
key hai   ->  Data API v3  ->  fail/quota khatam  ->  public page
key nahi  ->  public page
```

Fayda: API ka contract nahi badalta, page ka markup YouTube kabhi bhi badal sakta
hai. Aur quota khatam ho jaye to service band nahi hoti — page se chalti rehti hai.
Response ka `method` field batata hai kaunsa raasta use hua: `api` ya `public-page`.

## Instagram — browser chalana padta hai

Instagram ka content **server-rendered hai hi nahi**. Plain fetch pe 620 KB ka khali
JS shell aata hai; asli data browser ke andar GraphQL call se aata hai. Ye probe karke
confirm kiya:

| Kya try kiya | Nateeja |
|---|---|
| `GET /p/{code}/` plain HTTP | 620 KB shell, koi timestamp nahi |
| `GET /p/{code}/embed/` | wahi shell |
| `api.instagram.com/oembed` | HTML wapas, JSON nahi |
| `graph.facebook.com/instagram_oembed` | 400, token ke bina nahi |

Par browser me render karo to time saaf DOM me hota hai, **bina login ke**:

```html
<time datetime="2026-08-25T17:29:13.000Z" title="Aug 25, 2026">
```

Isliye service headless Chrome chalati hai. DOM me **pehla `<time>` post ka apna**
hota hai, baaki comments aur related posts ke.

### Ye sahi hai — verify kaise kiya

18 asli public posts (nasa, natgeo, bbcnews) pe chalaya, aur do tarike se check kiya:

1. **Media ID ordering** — har shortcode se media ID decode karke posts ko ID order me
   lagaya. Extracted timestamps 18/18 ascending nikle, ek bhi break nahi. Agar hum kisi
   comment ka time pakad rahe hote to ye order toot jaata.
2. **Cross-config** — slow render (12s budget) aur fast render (6s budget) ke jawab
   18/18 identical aaye.

Code me teen guard hain: Oct 2010 se pehle ka time reject (IG tab bana), future ka
reject, aur login-wall/deleted-post alag se detect hote hain. Markup badal jaye to
service `upstream_error` deti hai — chup-chaap galat jawab nahi.

### Kitna mehenga hai

| | |
|---|---|
| Per post | ~6.4s (ek Chrome process) |
| Throughput | ~9-10 posts/minute per machine |
| Concurrency | 6 parallel requests = 38.8s wall clock — utna hi jitna sequential |

Concurrency se throughput **nahi** badhta — kaam CPU-bound hai, browser render karne me.
`HEADLESS_MAX_CONCURRENT` (default 4) sirf memory ka cap hai, speed ka nahi. Zyada
throughput chahiye to zyada machines chahiye, zyada threads nahi.

Ye pehle 17s per post tha. Images/fonts band karne se aur **desktop window size** rakhne
se 6.4s hua. Chhota window (400x600) Instagram ko mobile layout pe bhej deta hai jisme
`<time>` hota hi nahi — wo trap hai, dekhne me tez lagta hai par galat jawab deta hai.

### Token optional hai

`IG_ACCESS_TOKEN` set karoge to apne account ke posts Graph API se turant aayenge
(~200ms), aur browser sirf baaki posts ke liye chalega. Response ka `method` batata hai:
`api` ya `headless-page`.

### Ye kya nahi hai

- **Volume pe tested nahi.** ~45 requests pe koi block nahi laga. Hazaron pe Meta
  zaroor reagega — production me proxy pool ya rate limiting chahiye hogi.
- **Ek heuristic hai, contract nahi.** Instagram DOM kabhi bhi badal sakta hai.
- **Private accounts pe nahi chalega** — wahan login wall asli hai. Service
  `404 not_visible` deti hai.

## Facebook — bhi ho gaya

Pehle maine ise "possible nahi" bola tha. Wo galat tha, aur galti do thi:

1. Maine Facebook ka **page** (`facebook.com/NASA`) test kiya tha, **post permalink** nahi.
2. Maine sirf `<time>` aur `<abbr data-utime>` dhoonde the. Facebook wahan timestamp
   rakhta hi nahi — wo **embedded JSON** me hota hai.

Page ke usi dump me ye maujood tha, bas maine dekha nahi:

```
"creation_time":1788012882
"publish_time":1788012882
```

### Kya chalta hai

Ek hi post ke ye saare URL forms test kiye — sab **same timestamp** dete hain:

| URL form | Chalta hai |
|---|---|
| `/<page>/posts/<numeric id>` | haan |
| `/reel/<id>` | haan |
| `/watch/?v=<id>` | haan |
| `/<numeric id>` | haan |
| `/permalink.php?story_fbid=…` | haan |
| `/<page>/posts/<pfbid…>` | **nahi** — neeche dekho |

### Ye sahi hai — verify kaise kiya

- **Stability**: ek hi permalink teen baar render kiya — teeno baar bilkul same jawab.
- **Self-consistency**: permalink pe `creation_time` aur `publish_time` dono
  **exactly ek-ek baar** aate hain aur equal hote hain. Page listing pe iske ulat
  kai posts ke timestamps hote hain — isliye service sirf permalink render karti hai.
- **Identity**: requested post_id DOM me 4 baar milta hai. Service pehle yahi check
  karti hai — agar requested id page pe nahi mila to `404 not_visible`, timestamp
  uthane ki koshish hi nahi.
- **Ambiguity**: DOM me ek se zyada alag `creation_time` mile to service guess **nahi**
  karti, error deti hai. Galat jawab dene se behtar hai jawab na dena.

### pfbid links

Facebook ka "Copy link" `pfbid…` waala URL deta hai. Mere test me wo resolve nahi hua,
par mera token page ke JS blob se scrape kiya hua tha — asli share-link nahi tha
(bare pfbid ne kisi aur ki profile khol di, matlab wo post token tha hi nahi).
Service pfbid URL ko accept karti hai aur browser ko waise ka waisa deti hai; FB use
resolve kar de to time mil jayega, warna `404 not_visible` milega.

Numeric ID waala form pakka chalta hai. Wo post pe date pe click karne se URL me
aa jaata hai.

### Kitna mehenga hai

~8-14 second per post — Instagram se thoda zyada. Wahi concurrency limit lagti hai.

## Alag-alag deploy karna

Har service apne aap me poori hai, to unhe alag-alag chala sakte ho:

```bash
PLATFORMS=x,linkedin uvicorn app.main:app --port 8001   # offline, network chahiye hi nahi
PLATFORMS=youtube    uvicorn app.main:app --port 8002   # ye outbound network use karta hai
```

X aur LinkedIn ko network chahiye hi nahi, to unke container ko outbound access
aur secrets dono se kaat sakte ho.
UI `GET /platforms` se banta hai, to jo deploy hua hai wahi dikhega.

## Error contract

| HTTP | error | matlab |
|---|---|---|
| 400 | `unsupported_url` | URL us platform ka valid post link nahi |
| 400 | `wrong_platform` | Link kisi aur platform ka hai (`expected` + `actual` bhi aate hain) |
| 404 | `not_visible` | Token ke paas is post ka access nahi |
| 422 | `invalid_id` | ID mila par timestamp plausible nahi |
| 502 | `upstream_error` | Platform ki API down ya quota khatam |
| 503 | `not_configured` | Us platform ka key/token set nahi |
| 503 | `disabled` | Platform is deployment me mounted nahi |

## Naya platform add karna

1. `app/platforms/<naam>.py` — `Platform` extend karo, `match()` aur `timing()` likho.
2. `app/platforms/__init__.py` ke `CATALOG` me add karo.

Route, `/platforms`, aur UI ka picker — teeno apne aap update ho jaate hain.

## Code map

```
app/platforms/base.py     Platform contract + errors
app/platforms/*.py        ek file = ek platform service
app/platforms/__init__.py registry: CATALOG, enabled(), detect()
app/resolvers/snowflake.py  offline timestamp maths (X, LinkedIn)
app/resolvers/youtube.py    public watch page + Data API v3
app/resolvers/graph.py      Meta Graph API (IG, FB)
app/resolvers/browser.py    headless Chrome (concurrency capped)
app/resolvers/instagram_page.py  rendered DOM se timestamp
app/resolvers/facebook_page.py   embedded JSON se creation_time
app/service.py            resolve_with(platform, url) / resolve(url)
app/main.py               per-platform routers + shared endpoints
app/web/index.html        UI (picker → URL → result)
```

## Aage kya

- Rate limiting per IP (slowapi)
- Instagram ke liye proxy pool, agar volume badhe
- Reddit, TikTok, Threads services
