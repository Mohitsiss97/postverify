"""Create demo campaigns against a running portal, for manual testing.

This is a development tool. It is not copied into the production image and
nothing in the application imports it.

Three campaigns are created, each one exercising a different outcome so that the
whole flow can be walked through by hand:

    1. A realistic 24-hour campaign, for posting something yourself.
    2. A wide-window campaign holding the creative that matches a post you
       already have, so a correct submission is approved.
    3. A wide-window campaign holding a different creative, so a submission is
       rejected for image mismatch.

Campaigns 2 and 3 use a 30-day window deliberately. The point of those two is to
demonstrate the *image* decision, and a 24-hour window would reject an older
test post before the image was ever compared.

Usage:

    python scripts/seed_demo.py matching.jpg different.jpg
    python scripts/seed_demo.py matching.jpg different.jpg --url http://localhost:8300

`matching.jpg` should be the image from a real public post you can submit;
`different.jpg` should be anything else.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import httpx

DAY = 24
MONTH = 24 * 30


def create(client: httpx.Client, title: str, description: str, window_hours: int,
           image: pathlib.Path | None) -> int:
    """Create a campaign, attach a creative, and activate it.

    A campaign with no creative cannot be activated — every submission to it
    would be rejected — so the one without an image is deliberately left as a
    draft for the operator to fill in.
    """
    campaign = client.post("/v1/campaigns", json={
        "title": title,
        "description": description,
        "window_hours": window_hours,
    }).raise_for_status().json()

    if image is None:
        print(f"  {campaign['id']:>3}  draft   {window_hours:>4}h  {title}")
        print("       add a creative, then activate it")
        return campaign["id"]

    client.post(f"/v1/campaigns/{campaign['id']}/assets",
                files={"file": (image.name, image.read_bytes(), "image/jpeg")}
                ).raise_for_status()
    client.patch(f"/v1/campaigns/{campaign['id']}",
                 json={"status": "active"}).raise_for_status()

    print(f"  {campaign['id']:>3}  active  {window_hours:>4}h  {title}")
    return campaign["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matching", type=pathlib.Path,
                        help="the image from a public post you can submit")
    parser.add_argument("different", type=pathlib.Path,
                        help="any other image, to demonstrate a mismatch")
    parser.add_argument("--url", default="http://localhost:8300",
                        help="the portal's base URL")
    parser.add_argument("--admin-token", default=None,
                        help="required if the portal has ADMIN_TOKEN set")
    args = parser.parse_args()

    for path in (args.matching, args.different):
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return 1

    headers = {"X-Admin-Token": args.admin_token} if args.admin_token else {}
    with httpx.Client(base_url=args.url, timeout=60, headers=headers) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as e:
            print(f"the portal is not reachable at {args.url}: {e}", file=sys.stderr)
            return 1

        print("Created:")
        create(client, "Festive Launch Campaign",
               "Download the creative, post it to your own account, and submit "
               "the link within 24 hours.",
               DAY, args.matching)
        create(client, "Demo — approval path",
               "Holds the creative that matches the demo post. A correct "
               "submission here is approved. The 30-day window keeps the "
               "decision about the image rather than the timing.",
               MONTH, args.matching)
        create(client, "Demo — image mismatch path",
               "Holds a different creative, so a submission is rejected for "
               "image mismatch even though the timing is fine.",
               MONTH, args.different)

    print(f"\nOpen {args.url} to walk through the flow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
