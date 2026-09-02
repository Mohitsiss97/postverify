# Architecture

How the system is put together, and why the decisions that are hard to reverse
went the way they did.

---

## The shape of a submission

```
participant submits a link
        │
        ▼
  POST /v1/submissions ──▶ saved as `pending`, returns 202 immediately
        │                  (nothing slow happens on the request thread)
        ▼
  background worker claims it  ──▶ status `verifying`
        │
        ▼
  one call to postverify-api  ──▶ publish time + image comparison
        │
        ▼
  the rules, in order ──▶ approved | rejected | retry later
        │
        ▼
  a verification_record is written, plus an evidence file
```

A submission returns `202 Accepted` rather than a result because verifying an
Instagram or Facebook post takes around fifteen seconds. Holding a request open
for that long is poor behaviour towards the participant, and worse towards the
server: a handful of concurrent submissions would occupy every request worker
the application has. The UI polls `GET /v1/submissions/{id}` and shows progress.

---

## The rules, and why they run in this order

From [`campaign-portal/app/verification.py`](../campaign-portal/app/verification.py):

| # | Check | Cost |
|---|---|---|
| 1 | Is the campaign open? | database |
| 2 | Does it have creatives? | database |
| 3 | **One call to the engine** | ~15s on Instagram, ~0s on X |
| 4 | Does the platform match what was declared? | free, from that response |
| 5 | Is the post inside the window? | free |
| 6 | Has this post been submitted before? | database |
| 7 | Does the image match? | already done by step 3 |

The single engine call returns both the timestamp and the image comparison, so
steps 4 to 7 are all paid for at step 3. The ordering still matters: if the
timing already disqualifies the post, there is no point trying the campaign's
*other* creatives, and each of those would be another fifteen seconds.

The other reason for this order is what the participant is told. A post that is
both too old *and* carries the wrong image is reported as too old, because that
is the thing they can act on first.

### Retryable versus final

A rejection is a judgement about the submission and is final — running "the
image did not match" again will not produce a different answer, and each retry
costs another render. Only two reasons are retried, both technical:
`engine_unavailable` and `time_not_available`. Retries back off exponentially,
capped at an hour, and stop after `MAX_ATTEMPTS`.

This distinction lives in one place, `RETRYABLE` in
[`app/enums.py`](../campaign-portal/app/enums.py), alongside the participant-facing
message for every reason. Adding a reason means editing that one file.

---

## Deduplication

A post counts exactly once across the whole system. That is enforced by the
database, not by application logic, because two workers can decide
simultaneously.

`submissions.dedupe_key` holds `platform:post_id` and carries a unique
constraint. The column is **nullable, and only populated while the submission is
live** (pending, verifying or approved). On rejection it is set back to NULL.

Two consequences follow, both intended:

- A rejected post is released. The same participant can fix the problem — make
  the post public, repost it in time — and submit the same link again.
- NULLs do not collide in a unique index, in PostgreSQL or SQLite, so hundreds of
  rejected submissions coexist without a partial index or a database-specific
  clause.

When two submissions do race, one commit raises `IntegrityError` and that
submission is marked a duplicate. The database is the arbiter; there is no lock
to acquire and no window in which both can win.

---

## The verification engine

`postverify-api` answers two questions about a public post URL, with no login
and no API key.

### Reading the publish time

What each platform exposes was established empirically, not assumed:

| Platform | Method | Cost |
|---|---|---|
| X (Twitter) | The status ID is a snowflake; the upper 41 bits are a millisecond timestamp | **No network call at all** |
| LinkedIn | Same, with a plain Unix epoch | **No network call at all** |
| YouTube | `<meta itemprop="uploadDate">` on the public watch page | One HTTP fetch |
| Instagram | The first `<time>` element, after rendering | Headless browser |
| Facebook | `"creation_time"` in embedded JSON, after rendering | Headless browser |

Instagram and Facebook return an empty JavaScript shell to a plain fetch, which
is why they need a real browser. Two findings from getting that working are
worth keeping:

- **A desktop window size is mandatory.** At 400×600 Instagram serves its mobile
  layout, which has no `<time>` element at all — the render succeeds and the
  extraction silently finds nothing.
- **Disabling images and fonts takes a render from ~16s to ~6s** and leaves the
  DOM identical.

Facebook's timestamp appears exactly once on a permalink, but a page listing
carries the timestamps of many posts. When more than one distinct value is
present the extractor raises rather than guessing which one belongs to the post.

### Comparing the image

Three levels, cheapest first
([`app/compare.py`](../postverify-api/app/compare.py)):

1. **SHA-256** — the same file, byte for byte. Instant and certain.
2. **Perceptual hash** — visually the same. Survives resizing and recompression;
   fails under cropping.
3. **ORB keypoints + RANSAC homography** — still the same after cropping,
   rotation or a watermark. This does the real work.

The finding that made this reliable: **a genuine match is identified by the
inlier _ratio_, not the inlier count.** When the image really is the same, nearly
every good keypoint match agrees on one geometric transform — a ratio of
0.87–1.00. Two unrelated images do produce a handful of coincidental matches, but
those never agree on a single transform — 0.33–0.50. There is a clean gap between
the two populations, and the thresholds sit in it.

Calibrated on twenty variants each of seven real images: zero misses, zero false
positives. The known limit is a crop below roughly 30% of each dimension.

---

## Data and storage

Five tables. The one distinction worth explaining is between a submission and a
verification record:

- **`submissions`** — one row per link a participant sent, holding the single
  status they see. It survives retries as one row.
- **`verification_records`** — one row per *attempt*. This is the audit trail.

The record copies the creative's SHA-256 rather than only referencing it, so that
when an administrator later replaces a campaign image, the record still says
which image was actually compared at the time.

Alongside the database, each attempt writes a JSON evidence file to
`STORAGE_DIR/evidence/<submission_id>/attempt-N.json` containing the post URL,
the checked creative's hash, the outcome and the engine's complete response.
Failing to write it does not fail the verification — the outcome still reaches
the database either way.

Campaign creatives live in `STORAGE_DIR/assets/<campaign_id>/<sha-prefix>.jpg`.
The filename is derived from the content, so the same bytes always produce the
same file whatever the upload was called.

**What is never stored:** the image from the participant's post. The engine
downloads it into memory, compares it, and discards it when the request ends. It
is never written to disk by either service.

### Timestamps

Every timestamp column uses `UtcDateTime`, a `TypeDecorator` that normalises to
UTC on the way in and on the way out. This is not tidiness. SQLite does not store
timezones and returns naive datetimes; PostgreSQL returns aware ones. Without
this, code works in development and fails in production with `can't compare
offset-naive and offset-aware datetimes` — a divergence that only appears after
deployment.

---

## Configuration and startup

Both services validate their configuration in the lifespan handler before
accepting any traffic, and distinguish two severities:

- A **warning** is logged for the operator to weigh: no rate limit, plaintext
  HTTP to a remote engine.
- A **fatal error stops the process**: no `ADMIN_TOKEN` in production, SQLite in
  production, a wildcard CORS origin.

The reasoning is that a misconfigured instance should never accept its first
request. Discovering the problem later, under load, is strictly worse than
failing to start.

The effective configuration is logged once at startup, with secrets reduced to
`*_set: true`. The first question about any misbehaving deployment is what it is
actually configured with.

---

## Errors

Every error from either service has the same shape:

```json
{"error": "too_old", "message": "This post is 30 hours old. Posts must be..."}
```

FastAPI wraps `HTTPException` in `{"detail": ...}` on its own, which produced two
shapes — validation errors flat, everything else nested — and forced clients to
write two parsers. Exception handlers in both services unwrap it into one.

Unhandled exceptions never reach the caller. Their text routinely contains file
paths, SQL and occasionally credentials; the caller gets a request ID to quote
and the full trace goes to the log.

## Request correlation

Every response carries `X-Request-ID`. An incoming one is honoured rather than
replaced, so a trace started by a gateway continues through the portal; the
portal then forwards it to the engine as `sub-<id>-a<attempt>`. One submission is
therefore followable across both services in the logs.
