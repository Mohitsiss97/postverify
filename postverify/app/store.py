"""Post ki images ka temporary store.

Kyun zaroori hai: platform ke CDN links aur embed iframes browser me aksar block
ho jaate hain (ad-blocker, referrer policy, hotlink protection). Isliye images
server pe download hoti hain aur apne hi origin se serve hoti hain — tab browser
ke paas rokne ki koi wajah nahi bachti.

Data ka jeevan chhota hai, aur uske teen ant hain:
    1. check poora hote hi session delete
    2. adhoore session TTL ke baad sweep me delete (default 15 minute)
    3. service band hote waqt poora root delete

User ki upload ki hui image **kabhi disk pe nahi likhi jaati** — wo sirf request
ki memory me rehti hai aur response ke saath chali jaati hai.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

_NAME = re.compile(r"^[0-9]{1,3}\.(jpg|png|webp|gif|bin)$")


def _ttl() -> int:
    try:
        return max(30, int(os.getenv("PREVIEW_TTL_SEC", "")))
    except ValueError:
        return 900          # 15 minute


@dataclass
class Session:
    token: str
    directory: Path
    created: float
    files: list[str] = field(default_factory=list)
    payload: object = None      # is session ka Prepared result

    def expired(self, ttl: int) -> bool:
        return (time.monotonic() - self.created) > ttl


class Store:
    """Session-wise temp files. Har session ka apna folder."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="postverify-"))
        self._sessions: dict[str, Session] = {}

    # -- lifecycle -------------------------------------------------------

    def create(self) -> Session:
        self.sweep()
        token = secrets.token_urlsafe(16)
        directory = self.root / token
        directory.mkdir(parents=True, exist_ok=True)
        session = Session(token, directory, time.monotonic())
        self._sessions[token] = session
        return session

    def get(self, token: str) -> Session | None:
        session = self._sessions.get(token)
        if session is None:
            return None
        if session.expired(_ttl()):
            self.drop(token)
            return None
        return session

    def drop(self, token: str) -> bool:
        """Session ka saara data mita do."""
        session = self._sessions.pop(token, None)
        if session is None:
            return False
        shutil.rmtree(session.directory, ignore_errors=True)
        return True

    def sweep(self) -> int:
        """Chhoote hue sessions saaf karo — jinpe user wapas hi nahi aaya."""
        ttl = _ttl()
        dead = [t for t, s in self._sessions.items() if s.expired(ttl)]
        for token in dead:
            self.drop(token)
        return len(dead)

    def drop_all(self) -> None:
        for token in list(self._sessions):
            self.drop(token)
        shutil.rmtree(self.root, ignore_errors=True)

    # -- files -----------------------------------------------------------

    def put(self, session: Session, index: int, data: bytes,
            suffix: str = "jpg") -> str:
        name = f"{index}.{suffix if suffix in ('jpg', 'png', 'webp', 'gif') else 'bin'}"
        (session.directory / name).write_bytes(data)
        session.files.append(name)
        return name

    def read(self, session: Session, name: str) -> bytes | None:
        path = self.path(session, name)
        return path.read_bytes() if path else None

    def path(self, session: Session, name: str) -> Path | None:
        """Naam validate karke hi path banate hain — warna ../.. se kahin bhi
        pahunchne ka raasta khul jaata."""
        if not _NAME.match(name):
            return None
        candidate = (session.directory / name).resolve()
        if not str(candidate).startswith(str(session.directory.resolve())):
            return None
        return candidate if candidate.exists() else None

    # -- diagnostics -----------------------------------------------------

    def stats(self) -> dict:
        files = sum(len(s.files) for s in self._sessions.values())
        return {"sessions": len(self._sessions), "files": files,
                "ttl_seconds": _ttl(), "root": str(self.root)}


store = Store()


def suffix_for(content_type: str | None, url: str) -> str:
    """Content-type ya URL se extension — bas serve karne ke liye."""
    kind = (content_type or "").lower()
    for candidate in ("jpeg", "jpg", "png", "webp", "gif"):
        if candidate in kind:
            return "jpg" if candidate == "jpeg" else candidate
    lowered = url.lower().split("?")[0]
    for candidate in ("jpg", "jpeg", "png", "webp", "gif"):
        if lowered.endswith("." + candidate):
            return "jpg" if candidate == "jpeg" else candidate
    return "jpg"
