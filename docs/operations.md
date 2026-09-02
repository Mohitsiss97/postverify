# Operations

Running the system: what to watch, what to do when something breaks, and where
the limits are.

---

## What to watch

### The four signals that matter

| Signal | Where from | What it tells you |
|---|---|---|
| Submissions stuck in `pending` | `GET /v1/admin/stats` | The worker is not running, or the engine is down |
| Rate of `engine_unavailable` | `by_reject_reason` in the same response | The engine is failing, not the participants |
| Engine `/ready` | The engine itself | Whether the browser is present |
| Verification duration | `duration_ms` in the logs | Renders slowing down before they start timing out |

`GET /v1/admin/stats` returns counts by status and by rejection reason, for one
campaign or across all of them. It is the cheapest health signal you have:

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://portal.example.com/v1/admin/stats
```

```json
{
  "by_status": {"approved": 812, "rejected": 137, "pending": 3, "error": 2},
  "by_reject_reason": {"too_old": 88, "image_mismatch": 41, "duplicate": 8}
}
```

A healthy campaign has a small, steadily draining `pending` count and rejection
reasons dominated by `too_old` and `image_mismatch` — those are participants
making ordinary mistakes. `engine_unavailable` in that list is *your* problem,
not theirs.

The `error` count is the one to alert on. A participant's mistake — an
unsupported link, a private post — is recorded as `rejected` with a reason, never
as `error`, precisely so that `error` stays a clean signal about the system
itself rather than about the people using it.

### Logs

With `LOG_JSON=true` each line is a JSON object. Every request carries a
`request_id`, and the portal forwards it to the engine as
`sub-<submission_id>-a<attempt>`, so one submission is traceable across both
services:

```
{"level":"INFO","logger":"portal.access","request_id":"sub-412-a1",
 "method":"POST","path":"/v1/submissions","status":202,"duration_ms":18}
```

Worth alerting on:

- `logger: "portal.worker"` at ERROR — the loop itself failed, not one submission
- `"returned N abandoned submission(s) to the queue"` — a worker died; if it
  recurs, find out why
- Any 5xx in `portal.access` or `postverify.access`

4xx and 5xx are deliberately logged at different levels: a 4xx is the caller's
problem, a 5xx is yours. Alert on the latter only.

---

## Capacity

The binding constraint is browser renders, and it is CPU-bound rather than
concurrency-bound. **Measured: six parallel renders took 38.8 seconds, which is
the same as running them one after another.** Raising
`HEADLESS_MAX_CONCURRENT` past the available cores buys nothing but memory use.

Per engine instance, in round numbers:

| Platform | Cost per verification |
|---|---|
| X, LinkedIn (time only) | No network call at all |
| YouTube | One HTTP fetch, well under a second |
| Instagram, Facebook | ~6 seconds of render, ~15 seconds end to end |

That works out to roughly **9–10 Instagram or Facebook verifications per minute
per instance**.

### When that is not enough

In order of how much they cost you:

1. **Have participants declare `asset_id`.** The bundled UI already does. Without
   it, a campaign with five creatives can cost five calls instead of one.
2. **Restrict platforms.** `PLATFORMS=x,youtube` removes the browser entirely.
3. **Add engine instances behind a load balancer.** The engine is stateless —
   nothing is stored, nothing is cached between requests — so this scales
   linearly. It is the right lever.
4. **Add portal workers.** Set `WORKER_ENABLED=false` on the web instances and
   run `python -m app.worker` as its own deployment, scaled separately. The
   claim-by-rowcount guard means multiple workers never take the same submission.
   Only do this once the engine can keep up; more workers in front of one engine
   just builds a queue.

Raising `WORKER_BATCH_SIZE` above the engine's `HEADLESS_MAX_CONCURRENT`
achieves nothing — the extra requests wait inside the engine instead of inside
the portal.

---

## Backups

Two things must be backed up **together**, because either one alone is
incomplete:

| What | Why |
|---|---|
| The PostgreSQL database | Campaigns, enrolments, submissions, verification records |
| `STORAGE_DIR` | Campaign creatives and the JSON evidence files |

The database records that submission 412 was approved after comparing against
asset 7. `STORAGE_DIR` holds the image that was, and the engine's full response
at the time. Restoring one without the other leaves an audit trail that cannot
actually be audited.

A daily `pg_dump` plus a volume snapshot is sufficient for most deployments.
Test the restore before you need it.

### Evidence retention

Evidence files are kept indefinitely; nothing prunes them. They are small —
roughly 1–2 KB each — so this is not urgent, but it is unbounded, and how long
they should be kept is a policy decision rather than a technical one.

Deleting old evidence files does not break anything: the verification records in
the database remain, and `evidence_path` simply points at a file that is gone.
The files are the detail behind the record, not the record itself.

---

## When something breaks

### Submissions stay `pending`

1. Is a worker running? Check `worker` in `GET /health` on the portal, or that
   the separate worker deployment is up.
2. Is the engine reachable? `GET /ready` on the portal reports both the database
   and the engine.
3. Look for `portal.worker` errors in the log.

### Everything fails with `engine_unavailable`

Usually one of three things:

- **The token does not match.** `ENGINE_TOKEN` on the portal must equal
  `ACCESS_TOKEN` on the engine. A mismatch produces a 401 that the portal maps
  to `engine_unavailable`, which is deliberately vague to the participant but
  clear in the logs.
- **The engine is unreachable.** Check `ENGINE_URL` and the network path.
- **The engine is rate-limiting the portal.** Raise `RATE_LIMIT_PER_MINUTE` on
  the engine, or set it to 0 if the engine is only reachable from the portal
  anyway.

Nothing is lost while this is happening. `engine_unavailable` is retryable, so
affected submissions back off and are retried; once the engine returns they
drain on their own.

### Instagram and Facebook fail, X and YouTube work

The browser is missing. `GET /ready` on the engine will show
`browser_available: false`. Install Chromium or set `CHROME_PATH`.

This is the failure most likely to go unnoticed, because the service looks
healthy and two platforms keep working.

### A submission is stuck in `verifying`

The worker holding it died. Recovery is automatic: after
`STALE_LOCK_MINUTES` (default 15) the row is returned to the queue and the log
records it. Nothing needs to be done by hand.

If it recurs, find out why the worker is dying — the container being killed
before its termination grace period elapsed is the common cause. Give containers
at least fifteen seconds; on shutdown the portal waits up to ten for the
submission in flight to finish.

### A participant says their submission was wrongly rejected

1. Open it in Administration → Review submissions and read the audit trail. Each
   attempt shows when it ran, which creative it compared against (by SHA-256),
   the verdict and the score.
2. `checked_asset_sha256` identifies the exact image used, even if the campaign
   creative has since been replaced.
3. The evidence file at `evidence_path` holds the engine's complete response.

If they are right, approve it by hand with a note. The override is recorded as
another attempt in the same trail, so the correction is as visible as the
original decision.

If the post was made public only after submitting, "Check again" requeues it
rather than needing a fresh submission.

---

## Routine tasks

**Rotate a token.** Set the new value on the engine's `ACCESS_TOKEN` and the
portal's `ENGINE_TOKEN` and restart both. Verifications during the gap fail with
`engine_unavailable` and retry on their own, so there is no data loss — but do
it in a quiet period.

**Close a campaign.** Set its status to `closed`. Submissions in flight finish
normally; new ones are refused with `campaign_closed`.

**Replace a creative mid-campaign.** Upload the new one, remove the old one. Past
verification records still name the image they actually used, so the audit trail
stays honest. Note that participants who already downloaded the old image will
now be rejected for `image_mismatch` — close the campaign and start a new one
instead if that matters.

**Migrate the database.** `alembic upgrade head`, as a separate step before the
application containers start. With more than one instance, never leave this in
the entrypoint: they will race for the version lock on every deploy.
