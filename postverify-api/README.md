# PostVerify API

Give it the URL of a public social media post. It tells you when the post was
published, whether that falls inside a window you specify, and whether a given
image appears in it.

No login, no platform API key, no picker — the platform is detected from the URL.

Supported: **X (Twitter) · Instagram · Facebook · LinkedIn · YouTube**

This is the verification engine behind [Campaign Portal](../campaign-portal/),
but it stands on its own and is documented here as an integration target.

---

## Three endpoints

| | | |
|---|---|---|
| `POST /v1/time` | a URL | when the post was published |
| `POST /v1/within` | a URL + windows | whether it falls inside `1d`, `7d`, `1m`… |
| `POST /v1/verify` | a URL + an image | whether that image is in the post |

Each also has a `GET` form, except `verify`, which carries a file upload.
Interactive documentation is at `/docs`.

### `POST /v1/time`

```bash
curl -X POST http://localhost:8200/v1/time \
  -H 'content-type: application/json' \
  -d '{"url": "https://x.com/NASA/status/1935477485525180417", "tz": "Asia/Kolkata"}'
```

```json
{
  "ok": true,
  "platform": "x",
  "post_id": "1935477485525180417",
  "canonical_url": "https://x.com/NASA/status/1935477485525180417",
  "time": {
    "published_at": "2025-06-18T15:22:41.123Z",
    "published_at_local": "2025-06-18T20:52:41.123+05:30",
    "age_seconds": 41231,
    "age_human": "11 hours old",
    "method": "id-embedded",
    "precision": "millisecond"
  }
}
```

`method` tells you how the answer was obtained, which is also how much it cost:
`id-embedded` means no network call was made at all.

### `POST /v1/within`

The endpoint most integrations actually want. Pass as many windows as you like:

```bash
curl -X POST http://localhost:8200/v1/within \
  -H 'content-type: application/json' \
  -d '{"url": "https://www.instagram.com/p/XXXXXXXX/", "within": "1d,3d,7d,15d,1m"}'
```

```json
{
  "within": {"1d": true, "3d": true, "7d": true, "15d": true, "1m": true},
  "within_detail": {"1d": {"seconds": 86400, "cutoff": "…", "within": true}},
  "checked_at": "2026-09-02T10:14:22Z"
}
```

With a single window you also get a plain `is_within` boolean, so there is no
nested object to dig through.

**Units:** `s`, `min`, `h`, `d`, `w`, `m`/`mo` (month), `y`. A bare number means
days.

> **`m` means month here, not minute.** Many parsers read it the other way. This
> was built for "is it within 1 month", and silently answering a different
> question is worse than being unconventional. Minutes are `min`.

### `POST /v1/verify`

```bash
curl -X POST http://localhost:8200/v1/verify \
  -F 'url=https://www.instagram.com/p/XXXXXXXX/' \
  -F 'image=@my-creative.jpg' \
  -F 'within=1d'
```

```json
{
  "image": {
    "checked": true,
    "present": true,
    "verdict": "same",
    "score": 94,
    "images_checked": 3,
    "matched": {"tier": "post", "score": 94, "orb_inliers": 61, "phash_distance": 12}
  },
  "is_within": true
}
```

- `present` is the answer. `verdict` is `identical`, `same`, `likely` or
  `different`; the first three all mean present.
- `score` is a 0–100 indicator for display. **The verdict is the decision, not
  the score.**
- `tier: "post"` means the matched image definitively belongs to this post;
  `"page"` means it was found on the post's page and could be a carousel slide
  or a related post.
- Use `image_url` instead of `image` for server-to-server calls.
- Passing `within` here saves a separate `/v1/within` call — on Instagram and
  Facebook that is a second browser render avoided.

### Errors

Every error has one shape, whatever went wrong:

```json
{"error": "unsupported_url", "message": "This URL does not match any supported platform: …"}
```

| Code | Status | Meaning |
|---|---|---|
| `unsupported_url` | 400 | Not a recognised platform URL |
| `bad_image` | 400 | The image could not be decoded |
| `invalid_request` | 422 | Validation failed; `fields` lists what |
| `invalid_id` | 422 | The post ID carries no timestamp (a pre-2010 tweet) |
| `not_visible` | 404 | Private, deleted, or behind a login |
| `no_media` | 404 | The post has no image |
| `unauthorized` | 401 | `ACCESS_TOKEN` is set and yours did not match |
| `rate_limited` | 429 | Retry after the `Retry-After` header |
| `not_configured` | 503 | A browser is needed and none is installed |
| `upstream_error` | 502 | The platform did not respond usefully |

Partial answers are returned rather than discarded. If the time can be read but
the image comparison fails, you get the time plus an `image.error`. `within` is
`null`, never `false`, when the time is unknown — a `false` there would read as
"the post is old" rather than "we do not know".

---

## How it works

| Platform | Publish time | Cost |
|---|---|---|
| X (Twitter) | Snowflake ID: upper 41 bits are a millisecond timestamp | **No network call** |
| LinkedIn | Same, plain Unix epoch | **No network call** |
| YouTube | `uploadDate` on the public watch page | One HTTP fetch |
| Instagram | The first `<time>` element, after rendering | Headless browser |
| Facebook | `creation_time` in embedded JSON, after rendering | Headless browser |

Image comparison runs three levels, cheapest first: SHA-256, then a perceptual
hash, then ORB keypoints with a RANSAC homography. The third survives cropping,
rotation and watermarking. See
[docs/architecture.md](../docs/architecture.md#comparing-the-image) for why the
inlier *ratio* rather than the count is what makes it reliable.

**Nothing is stored.** No database, no session, no temporary file. Images from
the post exist only in the memory of the request that fetched them.

---

## Running it

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --port 8200
```

Instagram, Facebook and LinkedIn need Chrome or Edge; it is found automatically,
or set `CHROME_PATH`. X and YouTube need no browser at all:

```bash
PLATFORMS=x,youtube uvicorn app.main:app --port 8200
```

Configuration is documented in
[docs/configuration.md](../docs/configuration.md#postverify-api); `.env.example`
lists everything.

### Meta endpoints

- `GET /health` — liveness
- `GET /ready` — readiness; 503 when a required browser is missing
- `GET /platforms` — what this deployment supports and whether each is ready

`/ready` is separate from `/health` deliberately: a load balancer should stop
routing to an instance whose browser has gone, but an orchestrator must not
restart the process over it, because restarting will not install Chrome.

## Tests

```bash
ruff check . && pytest -q      # 157 tests
```

No test touches a browser or the network.

## Throughput

Roughly **9–10 Instagram or Facebook verifications per minute per instance**. It
is CPU-bound, not concurrency-bound: six parallel renders were measured at 38.8
seconds, the same as running them sequentially. The service is stateless, so
scaling out is the lever that works. See
[docs/operations.md](../docs/operations.md#capacity).
