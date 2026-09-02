"""Headless Chrome — un platforms ke liye jinka content server-side aata hi nahi.

(PostTime service se copy kiya gaya hai, jaan-boojh kar — dono services ek doosre
pe depend na karein isliye. Wahan ka code chhua nahi gaya.)

Instagram ka page plain fetch pe khali JS shell hota hai; asli data browser ke
andar chalne ke baad aata hai. Uske liye ek real browser chalana padta hai.

Ye mehenga hai: ~6 second aur ek Chrome process per page. Isliye concurrency
capped hai — warna 20 request aate hi 20 Chrome khul jayenge aur machine baith
jayegi. Cap se zyada requests queue me wait karti hain; timeout pe 503 milta hai.

Config (sab optional):
    CHROME_PATH             Chrome/Edge ka path, agar auto-detect fail ho
    HEADLESS_MAX_CONCURRENT ek waqt me kitne browser (default 4)
    HEADLESS_TIMEOUT_SEC    ek page pe max intezaar (default 45)
    HEADLESS_WAIT_MS        page ko render hone ka time (default 6000)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from functools import lru_cache


class BrowserError(RuntimeError):
    pass


class BrowserNotAvailableError(BrowserError):
    """Machine pe Chrome/Edge mila hi nahi."""


class RenderTimeoutError(BrowserError):
    pass


# Chhota window Instagram ko mobile layout pe bhej deta hai jisme timestamp hota
# hi nahi — isliye desktop size zaroori hai. Images/fonts off karne se 16s se
# 6s ho jaata hai (naap kar dekha), aur DOM wahi rehta hai.
_FLAGS = (
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--blink-settings=imagesEnabled=false",
    "--disable-remote-fonts",
    "--window-size=1280,900",
    "--disable-extensions",
    "--disable-background-networking",
    "--no-first-run",
    "--no-default-browser-check",
)

_WINDOWS_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_MAC_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
_UNIX_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


@lru_cache(maxsize=1)
def chrome_path() -> str | None:
    """Chrome ya Edge dhoondo. Dono Chromium hain, flags same chalte hain."""
    explicit = os.getenv("CHROME_PATH")
    if explicit:
        return explicit if os.path.exists(explicit) else None

    if sys.platform == "win32":
        candidates = _WINDOWS_CANDIDATES
        local = os.getenv("LOCALAPPDATA")
        if local:
            candidates += (os.path.join(local, r"Google\Chrome\Application\chrome.exe"),)
    elif sys.platform == "darwin":
        candidates = _MAC_CANDIDATES
    else:
        candidates = ()

    for path in candidates:
        if os.path.exists(path):
            return path
    for name in _UNIX_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> bool:
    return chrome_path() is not None


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "")))
    except ValueError:
        return default


_sem: asyncio.Semaphore | None = None
_sem_loop: asyncio.AbstractEventLoop | None = None


def _semaphore() -> asyncio.Semaphore:
    """Ek hi semaphore per event loop — concurrent Chrome count ka cap."""
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        _sem = asyncio.Semaphore(_int_env("HEADLESS_MAX_CONCURRENT", 4))
        _sem_loop = loop
    return _sem


async def render(url: str) -> str:
    """URL ko browser me render karke final DOM lao."""
    exe = chrome_path()
    if not exe:
        raise BrowserNotAvailableError(
            "Chrome ya Edge nahi mila. Install kijiye, ya CHROME_PATH env var me "
            "uska poora path dijiye.")

    wait_ms = _int_env("HEADLESS_WAIT_MS", 6000)
    timeout = _int_env("HEADLESS_TIMEOUT_SEC", 45)

    async with _semaphore():
        # Har run apni profile directory me — warna user ka khula hua Chrome
        # aur ye ek doosre se takraate hain.
        profile = tempfile.mkdtemp(prefix="posttime-chrome-")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, *_FLAGS,
                f"--user-data-dir={profile}",
                f"--virtual-time-budget={wait_ms}",
                "--dump-dom", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as e:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise RenderTimeoutError(
                f"Page {timeout}s me render nahi hua") from e
        except OSError as e:
            raise BrowserError(f"Browser chal nahi paya: {e}") from e
        finally:
            shutil.rmtree(profile, ignore_errors=True)

    dom = out.decode("utf-8", errors="replace")
    if not dom.strip():
        raise BrowserError("Browser ne khali page return kiya")
    return dom
