"""Meta Graph API resolver (Instagram + Facebook).

Ye dono platforms URL-alone se solve nahi hote — post ID me timestamp encode
hi nahi hota. Iska sirf ek legit raasta hai: Graph API + ek access token.

Token SERVICE ka hota hai (env var), user se nahi maanga jaata — par uska
scope limited hai: Graph API sirf un posts ka data deta hai jinka access us
token ke paas hai (apna Business/Creator account, apna Page). Kisi random
public post ka timestamp Graph se nahi milta — ye Meta ki policy hai.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

GRAPH = "https://graph.facebook.com/v19.0"
_TIMEOUT = 10.0


class GraphError(RuntimeError):
    pass


class NotConfiguredError(GraphError):
    pass


class NotVisibleError(GraphError):
    """Token ke paas is post ka access nahi."""


async def _fetch(node: str, field: str, token: str,
                 client: httpx.AsyncClient | None = None) -> str:
    owns = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        r = await client.get(f"{GRAPH}/{node}",
                             params={"fields": field, "access_token": token})
        if r.status_code in (400, 403, 404):
            try:
                detail = (r.json().get("error") or {}).get("message") or r.text
            except Exception:
                detail = r.text
            raise NotVisibleError(
                f"Graph API ne mana kiya: {detail}. Aam wajah — ye post us account ka "
                f"nahi hai jiska access token configure hai.")
        r.raise_for_status()
        value = r.json().get(field)
        if not value:
            raise NotVisibleError(f"Graph API ne '{field}' return nahi kiya")
        return value
    except httpx.HTTPError as e:
        raise GraphError(f"Graph API reachable nahi: {e}") from e
    finally:
        if owns:
            await client.aclose()


def _parse(raw: str) -> datetime:
    # Graph ISO-8601: 2024-03-11T09:15:00+0000
    if raw.endswith("+0000"):
        raw = raw[:-5] + "+00:00"
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


_IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def shortcode_to_media_id(shortcode: str) -> int:
    """IG shortcode ek base64-ish encoded media pk hai.

    Ismein timestamp NAHI hota — ye sirf ID decode hai taaki Graph API ko
    query kar sakein.
    """
    n = 0
    for ch in shortcode:
        try:
            n = n * 64 + _IG_ALPHABET.index(ch)
        except ValueError as e:
            raise ValueError(f"shortcode me invalid character: {ch!r}") from e
    return n


async def instagram_timestamp(shortcode: str,
                              client: httpx.AsyncClient | None = None) -> datetime:
    token = os.getenv("IG_ACCESS_TOKEN")
    if not token:
        raise NotConfiguredError("IG_ACCESS_TOKEN set nahi hai")
    media_id = shortcode_to_media_id(shortcode)
    return _parse(await _fetch(str(media_id), "timestamp", token, client))


async def facebook_created_time(post_id: str,
                                client: httpx.AsyncClient | None = None) -> datetime:
    token = os.getenv("FB_ACCESS_TOKEN")
    if not token:
        raise NotConfiguredError("FB_ACCESS_TOKEN set nahi hai")
    return _parse(await _fetch(post_id, "created_time", token, client))
