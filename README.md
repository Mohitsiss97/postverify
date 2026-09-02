# PostVerify

Kisi social media post ka URL do — **kab upload hua** pata chal jata hai.
Ek image bhi do — bata deta hai **wo image us post me hai ya nahi**, aur kitne % match.

Platform chunna nahi padta; URL se khud pehchan leta hai.

```bash
cd postverify-api                 # sirf API
pip install -r requirements.txt
uvicorn app.main:app --port 8000  # docs: http://localhost:8000/docs
```

## Is repo me paanch folder hain

| Folder | Kya hai |
|---|---|
| **[campaign-portal/](campaign-portal/)** | **Poora product.** Campaign, creatives, user submissions, aur "sahi image + 24 ghante ke andar" ka faisla. |
| **[postverify-api/](postverify-api/)** | **Verification engine.** Teen endpoints — portal isi ko call karta hai. |
| [postverify/](postverify/) | Wahi engine, par web page ke saath — khud check karne ke liye. |
| [posttime/](posttime/) | Pehli service: sirf upload time. Reference ke liye. |
| [imagematch/](imagematch/) | Doosri service: sirf image match. Reference ke liye. |

Portal aur engine **alag services** hain, HTTP se jude hue. Wajah: engine browser
chalata hai (bhaari, CPU-bound), portal sirf HTTP aur DB (halka) — dono ko alag
scale karna chahiye.

`postverify` pehli do ka merge hai, plus ek ahem sudhaar: ek post pe browser
**ek hi baar** chalta hai (pehle time aur images ke liye do baar chalta tha).

`postverify-api` usi ka API-only roop hai — na web page, na session, na temp
files. Post ki images sirf request ki memory me aati hain.

Poori technical detail har folder ke apne README me hai.

## Kya support hai

| Platform | Time kahan se | Browser chahiye |
|---|---|---|
| X (Twitter) | status ID ke bits — offline, koi network call nahi | nahi |
| LinkedIn | activity URN ke bits — offline | images ke liye |
| YouTube | public watch page ka `uploadDate` | nahi |
| Instagram | rendered DOM ka `<time>` | **haan** |
| Facebook | rendered JSON ka `creation_time` | **haan** |

Sab kuch **public data** se — koi login, koi API key zaroori nahi.

## Integration API

[`postverify-api/`](postverify-api/) me teen endpoints hain:

```bash
# 1. post kab upload hua
curl -X POST <host>/v1/time \
  -H "content-type: application/json" \
  -d '{"url":"https://www.instagram.com/p/XXXX/"}'

# 2. post 1d / 3d / 7d / 15d / 1m ke andar ka hai ya nahi
curl -X POST <host>/v1/within \
  -H "content-type: application/json" \
  -d '{"url":"https://www.instagram.com/p/XXXX/","within":"1d,3d,7d,15d,1m"}'

# 3. ye image us post me hai ya nahi
curl -X POST <host>/v1/verify \
  -F "url=https://www.instagram.com/p/XXXX/" \
  -F "image=@meri.jpg"
```

```json
{"time":   {"published_at": "2026-08-25T17:29:13Z", "age_human": "7 din purana"},
 "within": {"1d": false, "7d": false, "15d": true, "1m": true},
 "image":  {"present": true, "verdict": "identical", "score": 100}}
```

Poori API docs: **[postverify-api/README.md](postverify-api/README.md)**
· interactive docs `<host>/docs`

## Poora product

Sirf verification chahiye to upar wali API kaafi hai. Par agar aapko wo **campaign
flow** chahiye jisme users creative download karke apne account pe post karte hain
aur portal unhe verify karta hai — wo [campaign-portal/](campaign-portal/) me hai:
campaigns, creatives, enrollments, submissions, background verification, duplicate
detection, aur poora audit trail.
---

# Live deploy karna

## Pehle ye jaan lijiye

**1. GitHub apne aap ise chala nahi sakta.** GitHub Pages sirf static files serve
karta hai; yahan Python chahiye, aur Instagram/Facebook ke liye Chrome bhi. Code
GitHub pe rahega, chalega kisi container host pe.

**2. Datacenter IP se Instagram/Facebook ka behaviour alag ho sakta hai.** Ye
sabse bada risk hai aur main iske baare me sach bata deta hoon: maine ye sab
**ghar ke IP se test kiya hai**, cloud se nahi. Meta datacenter IPs ko zyada
sakhti se dekhta hai — cloud pe login wall aane ke poore chance hain. Deploy ke
baad sabse pehle ek Instagram URL try kar ke dekhiye. `404 not_visible` aaye to
matlab yahi hua.

X, LinkedIn aur YouTube pe ye dikkat nahi hai — wo kahin se bhi chalenge.

**3. Chrome ko RAM chahiye.** 512 MB waale free tiers pe wo crash karega.
Kam se kam **1 GB**, aaram ke liye 2 GB.

**4. Public URL matlab koi bhi aapke server se scrape kar sakta hai.** Isliye
`ACCESS_TOKEN` set kijiye (neeche).

## Do raaste

### A. Halka — browser ke bina (kahin bhi chalega)

X, LinkedIn (time), YouTube. Chrome ki zaroorat hi nahi, image chhoti, koi
blocking risk nahi, har free tier pe chalega.

```
PLATFORMS=x,linkedin,youtube
```

Dockerfile me chromium waali layer hata dijiye — image ~400 MB chhoti ho jayegi.

### B. Poora — Chrome ke saath

Paanchon platforms. Isko chahiye: **1-2 GB RAM**, aur request timeout **60s+**
(Instagram 14-18 second leta hai).

| Host | Kaisa hai |
|---|---|
| **Google Cloud Run** | Sabse achha fit — 2 GB aasani se, 60 min tak timeout, scale-to-zero, kam traffic pe lagbhag free |
| Fly.io | Achha, 1 GB machine sasti |
| Railway | Chal jayega, usage-based |
| Render | Free/Starter 512 MB — Chrome ke liye kam pad jayega |

## Cloud Run pe (recommended)

```bash
cd postverify-api
gcloud run deploy postverify-api \
  --source . \
  --region asia-south1 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 120 \
  --concurrency 4 \
  --allow-unauthenticated \
  --set-env-vars "ACCESS_TOKEN=koi-lamba-random-string,HEADLESS_MAX_CONCURRENT=2"
```

`--concurrency 4` aur `HEADLESS_MAX_CONCURRENT=2` jaan-boojh kar kam rakhe hain:
har render CPU khaata hai, aur ek instance pe zyada parallel renders sirf sabko
slow karte hain (naapa hua hai — concurrency se throughput badhta hi nahi).

## Access token

`ACCESS_TOKEN` set karte hi `/prepare` aur `/verify` bina token ke `401` denge.

```bash
# UI kholne ke liye
https://your-app.run.app/?token=aapka-token

# API
curl -X POST https://your-app.run.app/prepare \
  -H "X-Access-Token: aapka-token" \
  -F "url=https://youtu.be/jNQXAC9IVRw"
```

Token URL me ek baar do — browser use yaad rakh leta hai. Local pe token set na
karo to service khuli rehti hai.

`GET /health` bata deta hai lock laga hai ya nahi:

```json
{"status": "ok", "locked": true, "store": {"sessions": 0, "files": 0}}
```

## Sab env vars

| Var | Default | Kaam |
|---|---|---|
| `PLATFORMS` | sab | Kaunse platforms chalein (`x,linkedin,youtube`) |
| `ACCESS_TOKEN` | khali | Set ho to token ke bina kuch nahi chalega |
| `CHROME_PATH` | auto | Chrome/Edge ka path |
| `HEADLESS_MAX_CONCURRENT` | 4 | Ek waqt me kitne browser |
| `HEADLESS_TIMEOUT_SEC` | 45 | Ek page pe max intezaar |
| `PREVIEW_TTL_SEC` | 900 | Chhoote hue session kitni der baad delete |
| `PORT` | 8000 | Host inject karta hai |
| `YOUTUBE_API_KEY` | khali | Optional — YouTube ka time API se (page fallback rehta hai) |

## Deploy ke baad turant check kijiye

```bash
curl https://your-app.run.app/health
```

Phir UI me ye teen daal ke dekhiye:

1. `https://youtu.be/jNQXAC9IVRw` — chalna hi chahiye (browser nahi lagta)
2. `https://x.com/elonmusk/status/1026872652290379776` — time offline aata hai
3. Koi Instagram post — **yahi asli test hai.** Chal gaya to sab theek. `not_visible`
   aaya to Meta ne cloud IP block kiya hai; tab `PLATFORMS` se instagram/facebook
   hata dijiye, ya residential proxy lagana padega.

## Data ka hisaab

Service kuch jama karke nahi rakhti:

- Post ki downloaded images check hote hi delete
- Chhoot jayein to TTL sweep (default 15 min)
- Service band ho to poora temp folder delete
- **User ki upload ki hui image kabhi disk pe likhi hi nahi jaati** — sirf memory me

`GET /health` me `store` batata hai us waqt kitna data pada hai.

## Ek baat ToS ki

Instagram aur Facebook ka data unke public page ko browser me render karke padha
jaata hai — wahi jo aap khud bina login ke dekh sakte ho. Ye unke Terms of Service
ke grey area me hai. Apne use ke liye ya chhote scale pe theek hai; bade paimane
pe public service banani ho to unki official API leni chahiye.

## Tests

```bash
cd campaign-portal && pytest -q    # 58 tests
cd postverify-api  && pytest -q    # 147 tests
cd postverify      && pytest -q    # 167 tests
cd posttime        && pytest -q    # 106 tests
cd imagematch      && pytest -q    # 67 tests
```

Har push pe GitHub Actions paanchon chala deta hai — `.github/workflows/tests.yml`.
