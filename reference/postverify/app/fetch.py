"""HTTP helpers — page laana aur image download karna."""
from __future__ import annotations

import re

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
}

# Ek image ki hadd — koi 200 MB ka file bhej ke service ko na bitha de.
MAX_IMAGE_BYTES = 25 * 1024 * 1024
TIMEOUT = 25.0

_OG = (
    re.compile(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"'),
    re.compile(r'<meta[^>]*content="([^"]+)"[^>]*property="og:image"'),
    re.compile(r'<meta[^>]*name="twitter:image"[^>]*content="([^"]+)"'),
)


class FetchError(RuntimeError):
    pass


class TooLargeError(FetchError):
    pass


def og_images(html: str) -> list[str]:
    """Page ke og:image / twitter:image tags — order preserve karte hue, bina duplicate."""
    out: list[str] = []
    for pattern in _OG:
        for url in pattern.findall(html):
            clean = url.replace("&amp;", "&").strip()
            if clean.startswith("http") and clean not in out:
                out.append(clean)
    return out


async def get_html(url: str, *, client: httpx.AsyncClient | None = None) -> str:
    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                         headers=HEADERS)
    try:
        r = await client.get(url)
        r.raise_for_status()
        return r.text
    except httpx.HTTPError as e:
        raise FetchError(f"Page nahi mila: {e}") from e
    finally:
        if owns:
            await client.aclose()


async def get_image(url: str, *, client: httpx.AsyncClient | None = None) -> bytes:
    """Image download karo, size cap ke saath."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                         headers=HEADERS)
    try:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            declared = r.headers.get("content-length")
            if declared and int(declared) > MAX_IMAGE_BYTES:
                raise TooLargeError(f"Image bahut badi hai ({int(declared) // 1024} KB)")
            chunks, total = [], 0
            async for chunk in r.aiter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise TooLargeError("Image bahut badi hai")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as e:
        raise FetchError(f"Image download nahi hui: {e}") from e
    finally:
        if owns:
            await client.aclose()
