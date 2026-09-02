# Campaign Portal

The product. Campaigns, enrolment, submissions, the verification rules, the
audit trail, and the web UI.

Verification itself is done by [postverify-api](../postverify-api/), a separate
service the portal calls over HTTP.

---

## The flow

```
Administrator                    Participant
─────────────                    ───────────
create a campaign
upload a creative
activate it
                                 join the campaign
                                 download the creative
                                 post it to their own account
                                 submit the link
                                        │
                                        ▼
                          the portal checks, in this order:
                            1. right platform?
                            2. posted within the window?
                            3. already submitted by someone?
                            4. is it the same image?
                                        │
                                        ▼
                            approved, or rejected with a reason
```

A submission returns `202 Accepted` immediately and is verified in the
background, because opening a post on Instagram or Facebook takes around fifteen
seconds. The UI polls for the result and shows progress.

---

## API

Interactive documentation at `/docs`. The web UI at `/` is a client of this API
and has no privileges of its own.

### Participant

| | |
|---|---|
| `POST /v1/campaigns/{id}/enroll` | Join a campaign. Enrolling twice is not an error. |
| `GET /v1/enrollments` | My enrolments |
| `POST /v1/submissions` | Submit a post link → `202`, status `pending` |
| `GET /v1/submissions/{id}` | Status, with the full audit trail |
| `GET /v1/submissions` | My submissions |

Identity is the `X-User-Id` header. See
[docs/security.md](../docs/security.md) — this is not authentication yet, and
the document is explicit about what that means.

```bash
curl -X POST http://localhost:8000/v1/submissions \
  -H 'X-User-Id: ravi' -H 'content-type: application/json' \
  -d '{"enrollment_id": 4,
       "post_url": "https://www.instagram.com/p/XXXXXXXX/",
       "platform": "instagram",
       "asset_id": 7}'
```

`asset_id` says which creative was posted. Supplying it makes verification a
single engine call; without it the campaign's creatives are tried one at a time,
and each attempt is another call.

### Administration

Requires `X-Admin-Token` when `ADMIN_TOKEN` is set, which is mandatory in
production.

| | |
|---|---|
| `POST /v1/campaigns` | Create, as a draft |
| `PATCH /v1/campaigns/{id}` | Update; also activates and closes |
| `POST /v1/campaigns/{id}/assets` | Upload a creative |
| `GET /v1/campaigns/{id}/assets/{id}/file` | Download one — this is what participants post |
| `GET /v1/admin/submissions` | All submissions, with filters |
| `GET /v1/admin/submissions/{id}` | One in full, every attempt |
| `POST /v1/admin/submissions/{id}/decide` | Approve or reject by hand, with a note |
| `POST /v1/admin/submissions/{id}/recheck` | Put it back in the queue |
| `GET /v1/admin/stats` | Counts by status and by rejection reason |

A campaign cannot be activated without at least one creative: every submission to
it would be rejected with `no_campaign_assets`, so it is refused up front.

### Statuses and reasons

`pending` → `verifying` → `approved` | `rejected` | `error`

`error` means the retries were exhausted on a technical failure. `rejected` is a
judgement about the submission and is final.

| Reason | Retried? |
|---|---|
| `too_old`, `image_mismatch`, `wrong_platform`, `duplicate` | No — the answer will not change |
| `post_not_found`, `no_image_in_post`, `unsupported_url` | No |
| `engine_unavailable`, `time_not_available` | **Yes**, with exponential backoff |

Every reason has a participant-facing message, all defined together in
[`app/enums.py`](app/enums.py). Adding a reason means editing that one file.

### Errors

One shape everywhere:

```json
{"error": "already_pending", "message": "One of your submissions is being checked…",
 "submission_id": 41}
```

---

## Layout

```
app/
  main.py           the application, middleware, error handlers, /health, /ready
  config.py         every setting, plus startup validation
  middleware.py     request IDs, access logs, rate limiting, security headers
  db.py             async engine, session, the UTC timestamp type
  models.py         five tables
  schemas.py        the API contract, separate from the tables on purpose
  enums.py          statuses, reject reasons, and their messages
  deps.py           identity, the admin guard, one error shape
  verification.py   THE RULES — the heart of the system
  processing.py     writing a decision down, and deciding whether to retry
  engine_client.py  the HTTP client for postverify-api
  worker.py         the background queue loop
  routers/          campaigns, submissions, admin
  web/index.html    the UI, one self-contained file
migrations/         Alembic; the schema is owned here, never by create_all
```

Two files repay reading first: `verification.py` for the rules and the order they
run in, and `models.py` for why a submission and a verification record are
separate things.

## Running it

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --port 8000        # needs the engine on :8200
```

Uses SQLite and creates its tables automatically. In production it uses
PostgreSQL and refuses to start on SQLite; see
[docs/deployment.md](../docs/deployment.md).

The worker runs inside the application process by default. To run it separately:

```bash
WORKER_ENABLED=false uvicorn app.main:app --port 8000
python -m app.worker
```

## Tests

```bash
ruff check . && pytest -q      # 83 tests
```

The engine is faked throughout, so the suite is fast and touches no network.
`tests/test_rules.py` is the one to read: each test is one business rule.

## Configuration

[docs/configuration.md](../docs/configuration.md#campaign-portal) documents every
setting; `.env.example` lists them with the defaults.

Four are worth knowing before anything else:

| | |
|---|---|
| `ADMIN_TOKEN` | Required in production. Without it the admin endpoints are open. |
| `DATABASE_URL` | Must be PostgreSQL in production. |
| `STORAGE_DIR` | Must be a persistent volume, or the audit trail is lost on restart. |
| `ENGINE_URL` / `ENGINE_TOKEN` | Where the engine is, and the secret it expects. |
