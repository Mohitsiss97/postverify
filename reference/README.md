# Reference implementations

These three services are **not deployed**. They are the development history of
the verification engine, kept because each one isolates a problem that the
production code now solves in one place.

| Folder | What it established |
|---|---|
| `posttime/` | Reading a post's publish time from a public URL, per platform. X and LinkedIn decode offline from the ID; YouTube, Instagram and Facebook require a rendered page. |
| `imagematch/` | Deciding whether a reference image is present in a post, tolerant of crop, resize and watermarking. Settled on SHA-256 → perceptual hash → ORB with a RANSAC homography, and on the inlier *ratio* rather than the inlier count as the discriminator. |
| `postverify/` | The two combined behind one auto-detecting entry point, with a browser UI used for manual exploration. |

`postverify-api/` supersedes all three. Nothing here is on the deployment path,
nothing here runs in CI, and no production code imports from these folders.

They remain useful for one thing: when a platform changes its markup and
extraction breaks, the per-platform probes in `posttime/` are the fastest way to
see what the page actually returns now.
