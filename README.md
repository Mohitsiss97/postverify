# Campaign Portal

A portal for running social media campaigns where participation has to be
verified rather than trusted.

An administrator publishes a campaign with an image. A participant downloads
that image, posts it to their own social media account, and submits the link.
The portal then answers three questions automatically:

1. **Was it posted in time?** The publish time is read from the post itself, not
   from anything the participant tells us.
2. **Is it the right image?** Compared against the campaign creative, tolerant of
   resizing, cropping, recompression and watermarking.
3. **Has this post already been used?** Each post counts exactly once, across all
   participants.

If all three pass, the submission is approved. If not, the participant is told
which check failed and what to do about it.

---

## The two services

```
                    ┌──────────────────────┐
   participant ────▶│   campaign-portal    │   campaigns, submissions,
     browser        │   :8000              │   rules, audit trail, UI
                    └──────────┬───────────┘
                               │  HTTP
                               ▼
                    ┌──────────────────────┐
                    │   postverify-api     │   URL -> publish time
                    │   :8200              │   URL + image -> match
                    └──────────┬───────────┘
                               │  headless Chrome / plain HTTP
                               ▼
              Instagram · Facebook · X · LinkedIn · YouTube
```

| Service | What it is | Why it is separate |
|---|---|---|
| [`campaign-portal/`](campaign-portal/) | The product: campaigns, enrolment, submissions, the verification rules, the audit trail and the web UI. | Light: HTTP and database work only. |
| [`postverify-api/`](postverify-api/) | The verification engine. Given a post URL it returns the publish time; given a URL and an image it says whether that image is in the post. | Heavy: runs a browser, CPU-bound, and must scale on its own terms. |

They are separate processes because their resource profiles have nothing in
common. One browser render costs roughly six seconds and one Chrome process; the
portal handles hundreds of requests in that time. Deploying them together would
mean scaling the cheap thing to keep up with the expensive one.

---

## Running it locally

Two terminals. The engine first, because the portal calls it.

```bash
# terminal 1 — the verification engine
cd postverify-api
pip install -r requirements-dev.txt
uvicorn app.main:app --port 8200

# terminal 2 — the portal
cd campaign-portal
pip install -r requirements-dev.txt
uvicorn app.main:app --port 8000
```

Then open <http://localhost:8000>.

Instagram, Facebook and LinkedIn need Chrome or Edge installed; the engine finds
it automatically, or set `CHROME_PATH`. X and YouTube need no browser at all, so
`PLATFORMS=x,youtube` removes the dependency entirely.

The whole stack, including PostgreSQL, also runs under Docker Compose — see
[docs/deployment.md](docs/deployment.md).

## Tests

```bash
cd campaign-portal  && ruff check . && pytest -q     #  83 tests
cd postverify-api   && ruff check . && pytest -q     # 157 tests
```

No test touches a browser or the network. If that ever changes, the suite has
acquired a dependency it should not have.

---

## Documentation

| Document | What it answers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How the pieces fit together, and why the significant decisions went the way they did |
| [docs/configuration.md](docs/configuration.md) | Every environment variable, what it does, and what it costs to get wrong |
| [docs/deployment.md](docs/deployment.md) | Going to production, step by step, with the checklist |
| [docs/operations.md](docs/operations.md) | Running it: monitoring, capacity, backups, and what to do when something breaks |
| [docs/security.md](docs/security.md) | What is protected, what is not yet, and what an attacker can and cannot do |
| [campaign-portal/README.md](campaign-portal/README.md) | The portal's API and internals |
| [postverify-api/README.md](postverify-api/README.md) | The engine's API, for integrating it on its own |

Both services publish interactive OpenAPI documentation at `/docs`.

---

## What this does not do yet

Stated plainly, because knowing the edges matters more than the feature list:

- **There is no user authentication.** A participant's identity arrives in the
  `X-User-Id` header and is taken at face value. The admin surface can be locked
  with a shared token, and is required to be in production. See
  [docs/security.md](docs/security.md) for what this means and where the change
  goes when real authentication is added.
- **Post ownership is not verified.** The portal confirms that a post exists,
  when it was published, and what image it contains. It does not confirm that
  the account which published it belongs to the participant who submitted it.
- **Image matching has a limit.** A crop below roughly 30% of each dimension
  stops being recognisable. That was measured, not guessed; see
  [docs/architecture.md](docs/architecture.md).
- **Throughput is bounded by the browser.** Around 9–10 renders per minute per
  engine instance, and adding concurrency does not help — it is CPU-bound.
  [docs/operations.md](docs/operations.md) covers what to do about it.
