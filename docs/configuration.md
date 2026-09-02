# Configuration

Every setting either service reads, what it does, and what it costs to get
wrong.

Both read plain environment variables. In development they also read a `.env`
file beside the service. In production, supply real environment variables so
that secrets never sit on disk inside an image.

**REQUIRED IN PRODUCTION** below means exactly that: with `ENV=production` the
process refuses to start without it. That is deliberate — a misconfigured
instance should never accept its first request.

---

## campaign-portal

### Application

| Variable | Default | Notes |
|---|---|---|
| `ENV` | `development` | `development`, `staging` or `production`. Setting `production` turns on the strict validation below. |
| `LOG_LEVEL` | `INFO` | |
| `LOG_JSON` | `false` | Set `true` in production. Logs there are read by a tool, not by a person with grep. |

### Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | SQLite file | **REQUIRED IN PRODUCTION** to be PostgreSQL: `postgresql+asyncpg://user:pass@host:5432/campaign_portal` |
| `DB_ECHO` | `false` | Logs every SQL statement. Debugging only; it is very loud. |

SQLite is refused in production because it serialises writes across the whole
file. A second worker process fails on lock contention, and the failure looks
like random timeouts rather than a configuration problem.

### Verification engine

| Variable | Default | Notes |
|---|---|---|
| `ENGINE_URL` | `http://localhost:8200` | Base URL of postverify-api. |
| `ENGINE_TOKEN` | empty | The engine's `ACCESS_TOKEN`. Must match, or every verification fails with `engine_unavailable`. |
| `ENGINE_TIMEOUT_SECONDS` | `120` | Instagram and Facebook drive a browser. Do not lower this below about 60 or legitimate verifications will time out and be retried, doubling the load. |

Plain `http://` to a *remote* engine is a startup warning, not a refusal: the
campaign creatives and the engine token cross that link in the clear. Inside a
private network or a Compose network it is legitimate, which is why the operator
is told rather than blocked.

### Verification rules

| Variable | Default | Notes |
|---|---|---|
| `SUBMISSION_WINDOW_HOURS` | `24` | Maximum post age, measured from the moment of submission. A campaign may override it. |
| `MAX_ASSETS_TO_TRY` | `5` | How many creatives to try when the participant did not say which one they posted. **Each attempt is another engine call** — five creatives is up to 75 seconds on Instagram. |

The UI always sends `asset_id`, so this cap only applies to API clients that
omit it.

### Worker

| Variable | Default | Notes |
|---|---|---|
| `WORKER_ENABLED` | `true` | `false` runs the portal without a worker; run `python -m app.worker` separately. See [operations.md](operations.md). |
| `WORKER_POLL_SECONDS` | `2` | How long to sleep when the queue is empty. |
| `WORKER_BATCH_SIZE` | `2` | Submissions per pass. Raising this above the engine's `HEADLESS_MAX_CONCURRENT` only builds a queue inside the engine. |
| `MAX_ATTEMPTS` | `4` | Retries apply to technical failures only. |
| `RETRY_BASE_SECONDS` | `30` | Exponential, capped at one hour. |

### Storage

| Variable | Default | Notes |
|---|---|---|
| `STORAGE_DIR` | `./storage` | Campaign creatives and verification evidence. **Must be a persistent volume in production.** |
| `MAX_UPLOAD_BYTES` | `26214400` (25 MB) | Per creative. |

An ephemeral `STORAGE_DIR` takes the campaign creatives and the entire audit
trail with it on the next container restart. Approved submissions then have
nothing to show if disputed, and active campaigns lose the images participants
are meant to download.

### HTTP

| Variable | Default | Notes |
|---|---|---|
| `CORS_ORIGINS` | empty | Comma-separated origins. Empty means same-origin only, which is all the bundled UI needs. `*` is **refused in production**. |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per client, on write endpoints only. `0` disables it. |
| `TRUST_PROXY_HEADERS` | `false` | Read the client IP from `X-Forwarded-For`. |

`*` is refused because the portal authenticates by header. A wildcard origin
would let any site a participant visits issue authenticated requests on their
behalf.

Only enable `TRUST_PROXY_HEADERS` behind a proxy you control. If the service is
also reachable directly, a caller can forge the header and get a fresh
rate-limit bucket per request.

The rate limiter is in-process, so each worker enforces its own share: four
workers at 120/min allow 480/min in total. It is a safety valve against one
caller flooding the queue, not a billing-grade quota. For an exact global limit,
enforce it at the proxy and set `RATE_LIMIT_PER_MINUTE=0` here.

### Authentication

| Variable | Default | Notes |
|---|---|---|
| `ADMIN_TOKEN` | empty | **REQUIRED IN PRODUCTION.** Sent as `X-Admin-Token`. |
| `USER_HEADER` | `X-User-Id` | Where the participant's identity is read from. |

The admin endpoints create campaigns and override submission decisions. Left
open, anyone who can reach the service can approve their own submissions, which
is why an empty value stops the process in production.

---

## postverify-api

### Deployment

| Variable | Default | Notes |
|---|---|---|
| `ENV` | `development` | `production` turns on strict validation. |
| `LOG_LEVEL` | `INFO` | |
| `JSON_LOGS` | on in production | |

### Access control

| Variable | Default | Notes |
|---|---|---|
| `ACCESS_TOKEN` | empty | **REQUIRED IN PRODUCTION.** Accepted as the `X-Access-Token` header, a `token` field, or a `token` query parameter. |
| `CORS_ORIGINS` | empty | Empty means no browser access at all, which is correct: this API is designed to be called server-to-server. `*` alongside an access token is **refused**. |

Every request to this service drives a browser and reaches out to a social
platform. Left open, anyone can generate that traffic from your IP address, and
the resulting block lands on you rather than on them.

### Rate limiting

| Variable | Default | Notes |
|---|---|---|
| `RATE_LIMIT_PER_MINUTE` | `60` | Per client, on `/v1/*`. `0` disables it. `/health`, `/ready` and `/platforms` are never limited, so health checks are never throttled. |
| `TRUST_PROXY_HEADERS` | `false` | As above. |

### Platforms

| Variable | Default | Notes |
|---|---|---|
| `PLATFORMS` | all | Comma-separated: `x,instagram,facebook,linkedin,youtube`. |

Narrowing this to `x,youtube` removes the browser dependency completely and lets
you delete the Chromium layer from the Dockerfile, saving roughly 400 MB of
image. A link to a *disabled* platform is still recognised and reported as
"X is not enabled in this deployment" rather than as an unrecognised URL.

### Browser

| Variable | Default | Notes |
|---|---|---|
| `CHROME_PATH` | auto-detected | Chrome or Edge. Both are Chromium and take the same flags. |
| `HEADLESS_MAX_CONCURRENT` | `4` | Browsers at once. **This is the real cap on throughput.** |
| `HEADLESS_TIMEOUT_SEC` | `45` | Maximum wait for one page. |
| `HEADLESS_WAIT_MS` | `6000` | Time allowed for the page to render. Below about 4000, Instagram starts returning pages with no `<time>` element. |

Raising `HEADLESS_MAX_CONCURRENT` past the available CPU cores does not increase
throughput — this was measured, and six parallel renders took the same wall-clock
time as six sequential ones. It does increase memory: budget roughly 300–500 MB
per concurrent browser.

`CHROME_PATH` is resolved once and cached, so changing it requires a restart.

### Optional

| Variable | Default | Notes |
|---|---|---|
| `YOUTUBE_API_KEY` | empty | When set, YouTube timestamps come from the Data API first. The public page remains as a fallback, so the key is genuinely optional and an API failure costs nothing. |

---

## Getting it wrong: the symptoms

| Symptom | Likely cause |
|---|---|
| Every submission ends `engine_unavailable` | `ENGINE_URL` unreachable, or `ENGINE_TOKEN` does not match the engine's `ACCESS_TOKEN` |
| Instagram and Facebook always fail, X and YouTube work | No browser. Check `/ready` on the engine: `browser_available` will be false. |
| Submissions sit at `pending` forever | No worker: `WORKER_ENABLED=false` and no separate worker process running |
| Creatives vanish after a deployment | `STORAGE_DIR` is not on a persistent volume |
| Random database timeouts under load | SQLite with more than one worker |
| `published_at_local` is always null | Missing timezone database — install `tzdata` |
| Verifications time out and retry endlessly | `ENGINE_TIMEOUT_SECONDS` too low for a browser render |
