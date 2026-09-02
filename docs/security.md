# Security

What is protected, what is not yet, and what someone could actually do about it.

This document is deliberately blunt about the gaps. A security note that only
lists the controls is worse than useless, because it reads as an assurance the
system cannot give.

---

## The most important gap: there is no user authentication

A participant's identity arrives in the `X-User-Id` header and is taken at face
value. **Anyone who can reach the portal can act as any participant** by sending
a different value.

What that lets someone do:

- Read another participant's submissions and enrolment
- Submit a post link on their behalf
- Enrol them in a campaign

What it does **not** let them do:

- Change the outcome. The verification rules are evaluated server-side against
  the real post. Claiming to be someone else does not make a post recent, make
  the wrong image match, or free up a post that has already been counted.
- Reach the admin surface, which requires `ADMIN_TOKEN` separately.

This was an accepted decision for the current stage, not an oversight. It is
acceptable when the portal is reachable only from a trusted network or behind an
authenticating proxy. **It is not acceptable on the open internet.**

### Where the fix goes

Identity is resolved in exactly one place:
[`campaign-portal/app/deps.py`](../campaign-portal/app/deps.py), in
`current_user()`. Adding JWT means decoding the token there and returning the
same user ID. No router, model or rule changes — the whole system takes identity
from that one function, and `user_ref` in the database is already an opaque
string that a subject claim slots straight into.

Until then, put an authenticating proxy in front and have it set `X-User-Id`
from the verified session, stripping any value the client sent.

---

## What is protected

### The admin surface

`ADMIN_TOKEN` gates campaign creation, creative uploads, manual approval and
rejection, and requeueing. Compared with `secrets.compare_digest`, so the check
does not leak information through timing.

The portal **refuses to start** in production without it. That is the single
most important control in the system: those endpoints can approve any submission.

Note that participant endpoints stay open when the admin surface is locked —
locking administration must not stop the campaign running.

### The verification engine

`ACCESS_TOKEN` gates every `/v1/*` endpoint, and the engine refuses to start in
production without one.

This is not primarily about data. Every request drives a browser and reaches out
to a social platform. An open engine is a free proxy for generating traffic
against Instagram and Facebook from *your* IP address, and the block that
follows lands on you.

The engine should not be reachable from the internet at all. The bundled Compose
file only exposes it inside the Compose network.

### Cross-user access

Enforced in the queries, not in a filter afterwards:

- Reading another participant's submission returns **404, not 403**. A 403 would
  confirm the ID exists.
- `GET /v1/submissions` joins through enrolment and only returns the caller's own.
- Submitting against someone else's enrolment returns 404 for the same reason.

These hold regardless of the identity gap above: they scope by whatever identity
was supplied.

### Uploads

- Only `image/jpeg`, `image/png` and `image/webp` are accepted.
- Capped at `MAX_UPLOAD_BYTES` (25 MB).
- Stored under a filename derived from the content hash, never from the
  uploaded filename, so a crafted name cannot escape the directory.
- The engine reads uploads into memory only; nothing is written to disk there.

### Errors

Unhandled exceptions never reach the caller. Their text routinely carries file
paths, SQL and sometimes credentials — the caller gets a request ID to quote,
and the full trace goes to the log. There is a test that specifically checks a
database password in an exception message does not appear in the response.

The startup configuration log reduces every secret to `*_set: true`, so
enabling it does not put secrets in your log aggregator.

### Rate limiting

Per-client caps on the portal's write endpoints and on the engine's `/v1/*`.
Read endpoints are never limited, because the UI polls for a submission result
and throttling that would work against the participant at the worst moment.

It is in-process, so each worker enforces its own share. It is a safety valve
against one caller flooding the queue, not a quota. For an exact global limit,
enforce it at the proxy.

The limit keys on the participant's identity when known, falling back to the
client IP — several people behind one office NAT share an address and should not
consume each other's allowance.

### Browser response headers

`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a `Referrer-Policy`,
and a `Content-Security-Policy` with `frame-ancestors 'none'` and
`base-uri 'none'`.

The portal's policy permits inline styles and scripts, because the UI is a
single self-contained HTML file. That is a real weakening, and it is only
acceptable because **the page renders no user-supplied HTML**: every value from
the API reaches the DOM through `textContent` or an escaping helper, never
`innerHTML` with untrusted content. If that ever changes, move the inline blocks
into files and drop `'unsafe-inline'`.

Add `Strict-Transport-Security` at the proxy, where TLS terminates.

---

## What the verification actually proves

Worth being precise, because the difference matters when a dispute arises.

**It proves:**

- A post exists at that URL and is publicly visible
- When it was published, read from the platform's own data
- That the campaign's image appears in it, tolerant of resizing, cropping,
  recompression and watermarking
- That this exact post has not been counted for anyone else

**It does not prove:**

- **That the participant owns the account that posted it.** Someone can submit a
  link to anyone's post. Deduplication means only the first submitter is
  credited, which limits the damage but does not eliminate it. Verifying
  ownership needs platform OAuth, which would change what the system is.
- That the post is still live. Verification is a point-in-time check; a post
  deleted afterwards stays approved.
- That the image was not reposted from elsewhere. Presence is the question the
  system answers, by design.

### The image matching limit

Calibrated on twenty variants each of seven real images: zero misses, zero false
positives. The known limit is a crop below roughly 30% of each dimension, at
which point too few keypoints survive.

That limit is a false *negative*, not a false positive — a heavy crop is rejected
rather than wrongly accepted. Failing closed is the right direction here, and a
participant who is rejected can be reviewed by hand.

---

## Data handling

| Data | Where it lives | Retention |
|---|---|---|
| Participant identifier | `enrollments.user_ref` | Life of the campaign |
| Submitted post URL | `submissions.post_url` | Indefinite |
| Campaign creatives | `STORAGE_DIR/assets/` | Indefinite |
| Verification evidence | `STORAGE_DIR/evidence/` | Indefinite, unpruned |
| **The image from the participant's post** | **Nowhere** | **Never stored** |

The last row is the one people ask about. The engine downloads the post's image
into memory, compares it, and discards it when the request ends. Neither service
writes it to disk. What is retained is the campaign's own creative and the hash
of the one that was compared against.

Post URLs are personal data in most jurisdictions — they identify an account. If
you are subject to a deletion regime, deleting an enrolment cascades to its
submissions and verification records; the evidence files under
`STORAGE_DIR/evidence/<submission_id>/` are not cascaded and must be removed
separately.

---

## Reporting a problem

Report security issues privately to the repository owner rather than opening a
public issue.
