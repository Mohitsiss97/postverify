# Campaign Portal

Users ko creative do, wo apne social account pe post karein, aur portal check kar
le ki **sahi image, sahi waqt** pe post hui hai ya nahi.

```
Admin campaign banata hai  ->  creative (image) upload karta hai
User enroll karta hai      ->  creative download karta hai
User apne account pe post karta hai
User post ka link submit karta hai
Portal check karta hai:
    post 24 ghante ke andar ki hai?     (submit ke waqt se)
    us post me wahi image hai?
    ye post pehle kisi ne to nahi di?
Sab pass -> approved. Warna saaf wajah ke saath rejected.
```

Verification khud nahi karta — [postverify-api](../postverify-api/) ko HTTP se
call karta hai. Dono alag services hain, isliye alag-alag scale hoti hain: engine
browser chalata hai (bhaari, CPU-bound), portal sirf HTTP aur DB (halka).

```bash
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --port 8000      # UI: /   ·   API docs: /docs
pytest -q                              # 59 tests
```

## Web UI

`/` pe ek UI hai — dono taraf ka kaam usi se ho jaata hai:

**User:** campaigns dekho &rarr; shaamil ho &rarr; creative download karo &rarr;
post karke link daalo &rarr; result live dikhta hai (2 second me poll hota hai,
Instagram pe 15-20 second lagte hain).

Result me teen tick/cross saaf dikhte hain — platform, post ka time, aur image —
taaki user ko pata chale **kya sahi tha aur kya galat**. Sirf "rejected" keh dena
kaafi nahi hota.

**Admin:** campaign banao, creative upload karo, activate karo; saare submissions
ek table me, aur kisi bhi row pe click karke poora audit trail — har attempt,
kis image se compare hua, kitna waqt laga.

UI API ka hi client hai — wahi endpoints call karta hai jo aap karenge. **API me
kuch nahi badla**; `/` pehle JSON deta tha (schema me tha hi nahi), ab UI deta hai.

Abhi auth nahi hai, isliye UI me upar do khaane hain: **"Main hoon"** (user id,
jo `X-User-Id` header banta hai) aur **"Admin token"** (khali chhod dijiye jab tak
`ADMIN_TOKEN` set na ho). Dono browser me yaad rehte hain.

Poora stack (portal + engine + Postgres): `docker compose up`

---

## Kaam kaise chalta hai

### 1. Admin — campaign aur creatives

```bash
# campaign banao (draft me banti hai)
curl -X POST localhost:8000/v1/campaigns \
  -H "content-type: application/json" \
  -d '{"title":"Diwali Campaign","window_hours":24}'

# creative add karo — yahi image user post karega
curl -X POST localhost:8000/v1/campaigns/1/assets -F "file=@diwali.jpg"

# ab activate karo
curl -X PATCH localhost:8000/v1/campaigns/1 \
  -H "content-type: application/json" -d '{"status":"active"}'
```

Bina creative ke campaign activate nahi hoti — warna har submission
`no_campaign_assets` pe reject hota, aur user ko samajh hi nahi aata kyun.

### 2. User — enroll, download, post, submit

```bash
# enroll
curl -X POST localhost:8000/v1/campaigns/1/enroll -H "X-User-Id: ravi"

# creative download karo
curl localhost:8000/v1/campaigns/1/assets/1/file -o creative.jpg

# ...apne Instagram pe post karo, phir link submit karo
curl -X POST localhost:8000/v1/submissions -H "X-User-Id: ravi" \
  -H "content-type: application/json" \
  -d '{"enrollment_id":1,
       "post_url":"https://www.instagram.com/p/XXXXXXXX/",
       "platform":"instagram",
       "asset_id":1}'
```

`202 Accepted` turant milta hai, status `pending`. Asli check peeche chalta hai.

### 3. Nateeja

```bash
curl localhost:8000/v1/submissions/1 -H "X-User-Id: ravi"
```

Sab sahi:

```json
{"status": "approved",
 "message": "Sab sahi hai — post time aur image dono match ho gaye.",
 "published_at": "2026-09-01T09:12:04Z",
 "within_window": true,
 "image_verdict": "identical", "image_score": 100,
 "matched_asset_id": 1}
```

Kuch galat:

```json
{"status": "rejected", "reason": "too_old",
 "message": "Ye post 7 din purani hai. Post 24 ghante ke andar ki honi chahiye — dobara post karke naya link daaliye.",
 "within_window": false}
```

---

## Checks aur unka order

Order **jaan-boojh kar** aisa hai — sasta pehle, mehenga baad me:

| # | Check | Fail pe reason | Kharcha |
|---|---|---|---|
| 1 | Campaign chalu hai? | `campaign_closed` | DB |
| 2 | Creatives hain? | `no_campaign_assets` | DB |
| 3 | **Engine ko ek call** | | ~15s (Instagram) |
| 4 | Declared platform = link ka platform | `wrong_platform` | muft |
| 5 | Post window ke andar | `too_old` | muft |
| 6 | Ye post pehle submit nahi hui | `duplicate` | DB |
| 7 | Image match hui | `image_mismatch` | agla creative = ek aur call |

Wajah: har engine call Instagram/Facebook pe ~15 second ka browser render hai.
Time ya platform fail ho raha ho to baaki creatives try karne ka koi matlab nahi —
isliye wo faisle image se pehle hote hain, chahe dono ek hi call se aate hain.

Isi wajah se **`asset_id` bhejna behtar hai**. User ne creative chuna hi tha, to
wo bata sakta hai kaunsa post kiya — tab ek hi call lagti hai. Na bataye to
campaign ke creatives ek-ek karke try hote hain (`MAX_ASSETS_TO_TRY` tak).

### Reject reasons

| reason | Matlab |
|---|---|
| `too_old` | Post window se purani |
| `image_mismatch` | Post ki image campaign waali se match nahi hui |
| `wrong_platform` | User ne kuch aur bataya, link kisi aur platform ka |
| `duplicate` | Ye post pehle hi kisi ne submit ki hai |
| `post_not_found` | Post private, deleted, ya khul hi nahi rahi |
| `no_image_in_post` | Post me koi image hai hi nahi |
| `unsupported_url` | Link kisi supported platform ka nahi |
| `campaign_closed` | Campaign band ho chuki |
| `manual_reject` | Admin ne review me reject kiya |

Har reason ka ek saaf Hindi message hai (`app/enums.py`) — sab ek hi jagah, taaki
API, DB aur user ko dikhne wala text kabhi alag na ho jaayein.

---

## Kuch faisle jo soch kar liye gaye

### Verification peeche chalti hai, request me nahi

Instagram pe ek post kholne me ~15 second lagte hain. Agar ye request ke andar
hota to user 15 second latka rehta, **aur** ek slow platform poore app ke request
workers kha jaata. Isliye submission turant save hoti hai aur worker use peeche
nipta deta hai.

Worker abhi app ke andar hi chalta hai — chhote deployment ke liye kaafi. Load
badhe to `WORKER_ENABLED=false` karke usi loop ko alag process me chalaiye
(`python -m app.worker`); code wahi rehta hai.

### Retry sirf takneeki fail pe

Engine down ho ya timeout ho — wo user ki galti nahi, to submission `pending` me
wapas chali jaati hai aur backoff ke saath dobara chalti hai.

Business rejection **final** hai. "Image match nahi hui" ko dobara chala kar jawab
nahi badlega, aur har retry ek 15-second render hai.

### Ek post duniya me ek hi baar

`dedupe_key` (`platform:post_id`) sirf **zinda** submissions pe rehti hai
(pending / verifying / approved). Reject hote hi hat jaati hai — taaki wahi post
koi aur bhej sake, ya wahi user sudhaar ke.

Us column pe unique index hai, isliye do users ek saath ek hi post approve nahi
kara sakte: jo race haarta hai use `duplicate` mil jaata hai. Sirf code me check
karte to race ki gunjaish rehti.

### Adhoora jawab bhi jawab hai

Image mismatch pe bhi `within_window: true` aata hai — user ko dikhna chahiye ki
**timing theek thi, sirf image galat**. Ye jaankari humare paas hoti hai;
chhupa dena user ko dobara galti karne deta.

### Har koshish ka record

`verification_records` me har attempt ka poora byora hai: kis creative se compare
hua (uska sha256 bhi), engine ka poora jawab, kitna waqt laga, kya nateeja.
Saath me `storage/evidence/<submission>/attempt-N.json`.

sha256 record me **copy** hota hai — admin baad me creative badal de, tab bhi
record batata hai us waqt kis image se check hua tha.

---

## Ek cheez jo abhi check **nahi** hoti

**Post user ke apne account pe hai — ye verify nahi hota.**

Portal ye dekhta hai ki image sahi hai aur time sahi hai. Ye nahi dekhta ki post
kisne ki. Matlab koi user kisi aur ka post URL daal sakta hai jisme wahi creative
ho, aur wo pass ho jayega — bas ek hi baar, kyunki duplicate rule doosri baar rok
dega.

Aap ye jaan kar chhoda hai. Baad me lagana ho to raasta ye hai: enrollment me
user ka handle (`@username`) liya jaye, aur post ke author se milaya jaye —
zyadatar platforms pe author URL ya page se mil jaata hai.

---

## Config

| Var | Default | Kaam |
|---|---|---|
| `DATABASE_URL` | SQLite file | Production me `postgresql+asyncpg://...` |
| `ENGINE_URL` | `http://localhost:8200` | postverify-api ka pata |
| `ENGINE_TOKEN` | khali | Engine pe `ACCESS_TOKEN` laga ho to wahi |
| `ENGINE_TIMEOUT_SECONDS` | 120 | Instagram/Facebook 15s+ lete hain |
| `SUBMISSION_WINDOW_HOURS` | 24 | Default window (campaign apna rakh sakti hai) |
| `MAX_ASSETS_TO_TRY` | 5 | `asset_id` na ho to itne creatives try honge |
| `WORKER_ENABLED` | true | false = worker alag process me |
| `WORKER_BATCH_SIZE` | 2 | Ek chakkar me kitni submissions |
| `MAX_ATTEMPTS` | 4 | Takneeki fail pe kitni koshish |
| `ADMIN_TOKEN` | khali | Set ho to admin endpoints iske bina 401 |
| `LOG_JSON` | false | Production me true |
| `STORAGE_DIR` | `./storage` | Creatives aur evidence |

### Production checklist

- `DATABASE_URL` Postgres pe — SQLite ek hi process ke liye theek hai
- `ADMIN_TOKEN` **zaroor** set kijiye; bina iske koi bhi campaign bana sakta hai
  aur submissions override kar sakta hai
- `ENV=production`, `LOG_JSON=true`
- `alembic upgrade head` deploy ke waqt (Docker image ye khud karta hai);
  `create_all` sirf dev/SQLite pe chalta hai
- `STORAGE_DIR` ko persistent volume pe rakhiye — creatives aur evidence wahin hain
- Engine ko apni RAM chahiye (Chrome), aur `shm_size: 1gb` — compose me set hai

---

## Endpoints

| | |
|---|---|
| `POST /v1/campaigns` | Campaign banao (admin) |
| `PATCH /v1/campaigns/{id}` | Activate / close / badlo (admin) |
| `POST /v1/campaigns/{id}/assets` | Creative upload (admin) |
| `GET /v1/campaigns/{id}/assets/{aid}/file` | Creative download (user) |
| `POST /v1/campaigns/{id}/enroll` | Enroll (user) |
| `POST /v1/submissions` | Post link submit (user) |
| `GET /v1/submissions/{id}` | Status + records (user) |
| `GET /v1/admin/submissions` | Sab submissions, filter ke saath |
| `POST /v1/admin/submissions/{id}/decide` | Manual approve/reject |
| `POST /v1/admin/submissions/{id}/recheck` | Dobara queue me daalo |
| `GET /v1/admin/stats` | Campaign ka haal |
| `GET /health` · `GET /ready` | Health (process) aur readiness (DB + engine) |

Errors ek hi shape me: `{"error": "...", "message": "..."}`

Interactive docs: `/docs`

---

## Auth

Abhi nahi hai — par jagah chhod kar banaya gaya hai:

- User ki pehchan `X-User-Id` header se aati hai, aur wo sirf `app/deps.py` ke
  `current_user()` me padhi jaati hai. JWT lagana ho to **sirf wahi function**
  badlega; routers ko haath nahi lagana padega.
- `ADMIN_TOKEN` set karte hi admin endpoints lock ho jaate hain.

---

## Code map

```
app/main.py           app, routers, error handlers, health/ready
app/config.py         settings (env se)
app/db.py             async engine, session, UtcDateTime
app/models.py         5 tables
app/schemas.py        API ka contract (models se alag)
app/enums.py          statuses, reject reasons, user-facing messages
app/engine_client.py  postverify-api ka HTTP client
app/verification.py   rules — checks ka order yahin hai
app/processing.py     decision -> DB, record, retry ka hisaab
app/worker.py         background queue loop
app/deps.py           current_user, require_admin, error shape
app/routers/          campaigns, submissions, admin
app/web/index.html    web UI (API ka client — usme koi business rule nahi)
migrations/           Alembic
```

## Tests

```bash
pytest -q     # 59 tests
```

Koi test asli engine ko call nahi karta — sab `FakeEngine` se chalte hain
(`tests/conftest.py`), warna suite network aur 15-second renders pe latak jaati.

`FakeEngine` ye bhi ginta hai ki kitni call hui — kai tests isi pe tike hain,
kyunki har call asal me ek 15-second render hai.
