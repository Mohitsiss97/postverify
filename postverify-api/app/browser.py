"""Headless Chrome, for the platforms whose content never arrives server-side.

Instagram serves a bare JavaScript shell to a plain HTTP fetch; the real data
only appears once the page has run in a browser. Reaching it therefore requires
driving a real browser.

That is expensive: roughly six seconds and one Chrome process per page. So
concurrency is capped — without a cap, twenty simultaneous requests open twenty
Chrome processes and the machine stops responding. Requests beyond the cap wait
in the queue and receive a 503 if they time out.

Configuration (all optional):
    CHROME_PATH             path to Chrome/Edge, if auto-detection fails
    HEADLESS_MAX_CONCURRENT browsers running at once (default 4)
    HEADLESS_TIMEOUT_SEC    maximum wait for one page (default 45)
    HEADLESS_WAIT_MS        time allowed for the page to render (default 6000)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from functools import lru_cache

from . import config


class BrowserError(RuntimeError):
    pass


class BrowserNotAvailableError(BrowserError):
    """No Chrome or Edge installation was found on this machine."""


class RenderTimeoutError(BrowserError):
    pass


# A small window pushes Instagram to its mobile layout, which carries no
# timestamp at all, so a desktop window size is mandatory. Disabling images and
# fonts takes a render from roughly 16s to 6s (measured) and leaves the DOM
# unchanged.
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
    """Locate Chrome or Edge. Both are Chromium, so the same flags apply."""
    explicit = config.chrome_path()
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


_sem: asyncio.Semaphore | None = None
_sem_loop: asyncio.AbstractEventLoop | None = None


def _semaphore() -> asyncio.Semaphore:
    """One semaphore per event loop; this is the cap on concurrent browsers."""
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        _sem = asyncio.Semaphore(config.headless_max_concurrent())
        _sem_loop = loop
    return _sem


async def render(url: str) -> str:
    """Render a URL in the browser and return the resulting DOM."""
    exe = chrome_path()
    if not exe:
        raise BrowserNotAvailableError(
            "Neither Chrome nor Edge was found. Install one, or set CHROME_PATH "
            "to its full path.")

    wait_ms = config.headless_wait_ms()
    timeout = config.headless_timeout_sec()

    async with _semaphore():
        # Each run gets its own profile directory, otherwise this collides with
        # any Chrome the operator already has open.
        profile = tempfile.mkdtemp(prefix="postverify-chrome-")
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
        except TimeoutError as e:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise RenderTimeoutError(
                f"The page did not render within {timeout}s") from e
        except OSError as e:
            raise BrowserError(f"The browser failed to start: {e}") from e
        finally:
            shutil.rmtree(profile, ignore_errors=True)

    dom = out.decode("utf-8", errors="replace")
    if not dom.strip():
        raise BrowserError("The browser returned an empty page")
    return dom
