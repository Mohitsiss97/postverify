# Deployment

Getting the two services into production, in order, with the reasoning for each
step.

---

## Before you start

Decide three things, because they are awkward to change later.

**Where the storage volume lives.** `STORAGE_DIR` holds campaign creatives and
the verification evidence. It must survive container restarts. An ephemeral path
loses the images participants are meant to download and the entire audit trail
along with them.

**How much verification throughput you need.** One engine instance manages
roughly 9–10 Instagram or Facebook verifications per minute, and that number is
CPU-bound rather than concurrency-bound. X and YouTube are effectively free. If
your campaigns are X-only, `PLATFORMS=x,youtube` removes the browser entirely
and the constraint disappears.

**Whether the engine faces the internet.** It should not. It is expensive to
call, and the only thing that needs to reach it is the portal.

---

## 1. Generate the secrets

Two shared secrets, both long and random:

```bash
export ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export ENGINE_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export DB_PASSWORD=$(python -c "import secrets; print(secrets.token_urlsafe(24))")
```

- `ADMIN_TOKEN` protects the portal's admin endpoints — creating campaigns,
  overriding decisions.
- `ENGINE_TOKEN` is the portal's credential for the engine. It goes in the
  engine's `ACCESS_TOKEN` and the portal's `ENGINE_TOKEN`; they must match.

Put them in your platform's secret store, not in a file in the image.

## 2. Provision PostgreSQL

```
DATABASE_URL=postgresql+asyncpg://portal:<DB_PASSWORD>@db:5432/campaign_portal
```

The `+asyncpg` driver is required; the application is async throughout. SQLite is
refused at startup in production.

## 3. Deploy the engine

Build from [`postverify-api/`](../postverify-api/). The image includes Chromium
because Instagram, Facebook and LinkedIn have no server-side content.

```
ENV=production
ACCESS_TOKEN=<ENGINE_TOKEN>
HEADLESS_MAX_CONCURRENT=2
RATE_LIMIT_PER_MINUTE=60
```

Two requirements that are easy to miss and produce confusing failures:

- **Shared memory.** Chromium crashes on larger pages with Docker's default
  64 MB. Set `shm_size: 1gb`, or `--shm-size=1g`.
- **Memory limit.** Budget 300–500 MB per concurrent browser plus the base
  process. At `HEADLESS_MAX_CONCURRENT=2`, 2 GB is comfortable.

Verify before moving on:

```bash
curl -s http://engine:8000/ready
# {"status":"ready","browser_available":true,"browser_required":true}
```

If `browser_available` is false, Chromium is missing or `CHROME_PATH` is wrong.
Instagram, Facebook and LinkedIn will fail while X and YouTube keep working,
which is a confusing partial failure to diagnose later.

## 4. Run the database migrations

```bash
alembic upgrade head
```

The bundled Dockerfile runs this in its entrypoint, which is correct for a single
instance. **With more than one instance, make it a separate deployment step** and
start the application containers afterwards — otherwise several instances race
for the Alembic version lock on every deploy.

The schema is owned by Alembic. `create_all()` exists in the code but only runs
against SQLite, for development and tests; using it in production would let the
live schema and the migration history diverge silently.

## 5. Deploy the portal

```
ENV=production
LOG_JSON=true
DATABASE_URL=postgresql+asyncpg://portal:<DB_PASSWORD>@db:5432/campaign_portal
ENGINE_URL=http://engine:8000
ENGINE_TOKEN=<ENGINE_TOKEN>
ADMIN_TOKEN=<ADMIN_TOKEN>
STORAGE_DIR=/srv/storage
```

Mount the persistent volume at `STORAGE_DIR`.

The portal refuses to start if `ADMIN_TOKEN` is missing, if `DATABASE_URL` is
still SQLite, or if `CORS_ORIGINS` is `*`. Read the startup log: it prints the
effective configuration, secrets reduced to `*_set: true`.

---

## With Docker Compose

The whole stack, for a single-host deployment:

```bash
cd campaign-portal
export ADMIN_TOKEN=... ENGINE_TOKEN=... DB_PASSWORD=...
docker compose up --build -d
docker compose logs -f portal
```

This brings up PostgreSQL, the engine and the portal, with the engine reachable
only from inside the Compose network and named volumes for both the database and
`storage`. See [`campaign-portal/docker-compose.yml`](../campaign-portal/docker-compose.yml).

---

## Health checks

Both services expose two endpoints, and the distinction matters.

| Endpoint | Answers | Point it at |
|---|---|---|
| `/health` | "the process is alive" | The orchestrator's liveness probe |
| `/ready` | "it can do the work" | The load balancer's readiness probe |

The portal's `/ready` checks the database and the engine. The engine's `/ready`
checks that a browser is present when one is needed.

**Do not point a liveness probe at `/ready`.** Restarting the portal will not
bring the database back, and restarting the engine will not install Chrome — it
will just produce a restart loop on top of an outage you already have.

---

## Behind a reverse proxy

Terminate TLS at the proxy. Then:

- Forward `X-Forwarded-For` and set `TRUST_PROXY_HEADERS=true` on the portal, so
  rate limiting sees real client addresses. Only do this if the service is not
  also reachable directly — otherwise the header can be forged.
- Allow request bodies up to at least `MAX_UPLOAD_BYTES` (25 MB by default), or
  creative uploads fail at the proxy with an error the portal never sees.
- Read timeouts need to exceed nothing in particular: no portal request is slow,
  because verification is a background job.
- Do not expose the engine.

The portal already sends `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy` and a `Content-Security-Policy`. Add `Strict-Transport-Security`
at the proxy, where TLS is terminated.

---

## Production checklist

Secrets and access:

- [ ] `ADMIN_TOKEN` set to a long random value
- [ ] `ENGINE_TOKEN` matches the engine's `ACCESS_TOKEN`
- [ ] The engine is not reachable from the internet
- [ ] `CORS_ORIGINS` empty, or an explicit list — never `*`
- [ ] TLS terminated at the proxy

Data:

- [ ] `DATABASE_URL` points at PostgreSQL
- [ ] `alembic upgrade head` has run
- [ ] `STORAGE_DIR` is on a persistent volume
- [ ] Backups cover **both** the database and `STORAGE_DIR` — see [operations.md](operations.md)

Engine:

- [ ] `/ready` reports `browser_available: true`
- [ ] `shm_size` at least 1 GB
- [ ] Memory limit allows 300–500 MB per concurrent browser

Operations:

- [ ] `ENV=production` and `LOG_JSON=true` on both services
- [ ] Liveness probes on `/health`, readiness probes on `/ready`
- [ ] Logs shipped somewhere searchable by `request_id`
- [ ] Startup log read once, to confirm the effective configuration

Then verify end to end: create a campaign, upload a creative, activate it, post
that image from a real account, and submit the link. An approval that comes back
with `within_window: true` and an image score proves every component in the chain
at once.

---

## Upgrading

1. Deploy the engine first when its version changed. The portal accepts both the
   current and the older error envelope from the engine, so a brief version skew
   during a rolling deploy is safe in that direction.
2. Run migrations as a separate step.
3. Deploy the portal.

On shutdown the portal signals its worker and waits up to ten seconds for the
submission in flight to finish, so a deploy does not strand one mid-verification.
Give containers a termination grace period of at least fifteen seconds. A
submission that is killed anyway stays `verifying` — see the recovery note in
[operations.md](operations.md).
