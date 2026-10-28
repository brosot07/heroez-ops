"""Password for the host PC's local pages (settings, contracts, knowledge).

* only a PBKDF2 hash is stored (config/owner.json, readable by Administrators/SYSTEM only)
* asked every time: the session cookie ends when the browser closes, after 30 minutes
  without activity, and after 12 hours at most
* 5 wrong passwords lock the page for 5 minutes
* forgotten password: a Windows administrator creates the file `reset_password` in the
  data folder (Start menu shortcut "Reset password Heroez Ops"); the next start clears it
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from .db import Store

ITERATIONS = 310_000
IDLE_SECONDS = 30 * 60
MAX_SECONDS = 12 * 3600
MAX_FAILS, LOCK_SECONDS = 5, 5 * 60
COOKIE = "hz_owner"


class OwnerAuth:
    def __init__(self, home: Path, store: Store):
        self.path = home / "config" / "owner.json"
        self.reset_flag = home / "reset_password"
        self.store = store
        self.fails: list[float] = []

    # -- password ---------------------------------------------------------------
    def apply_reset_flag(self) -> bool:
        if self.reset_flag.exists():
            self.path.unlink(missing_ok=True)
            self.reset_flag.unlink(missing_ok=True)
            self.store.x("DELETE FROM kv WHERE key LIKE 'owner_session:%'")
            self.store.audit("system", "owner_password_reset")
            return True
        return False

    def has_password(self) -> bool:
        return self.path.exists()

    def set_password(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("Password minimal 8 karakter.")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"algo": "pbkdf2_sha256", "iterations": ITERATIONS,
                                         "salt": salt.hex(), "hash": digest.hex()}), encoding="utf-8")
        self.store.x("DELETE FROM kv WHERE key LIKE 'owner_session:%'")       # log out everywhere
        self.store.audit("owner", "owner_password_set")

    def check_password(self, password: str) -> bool:
        if self.locked():
            return False
        try:
            rec = json.loads(self.path.read_text(encoding="utf-8"))
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(rec["salt"]), int(rec["iterations"]))
            ok = hmac.compare_digest(digest.hex(), rec["hash"])
        except (OSError, ValueError, KeyError):
            ok = False
        if not ok:
            self.fails = [t for t in self.fails if t > time.time() - LOCK_SECONDS] + [time.time()]
            self.store.audit("owner", "owner_login_failed")
        else:
            self.fails = []
            self.store.audit("owner", "owner_login")
        return ok

    def locked(self) -> bool:
        recent = [t for t in self.fails if t > time.time() - LOCK_SECONDS]
        return len(recent) >= MAX_FAILS

    # -- sessions -----------------------------------------------------------------
    @staticmethod
    def _key(token: str) -> str:
        return "owner_session:" + hashlib.sha256(token.encode()).hexdigest()

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        self.store.set_kv(self._key(token), json.dumps({"created": now, "seen": now}))
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        raw = self.store.get_kv(self._key(token))
        if not raw:
            return False
        s, now = json.loads(raw), int(time.time())
        if now - s["seen"] > IDLE_SECONDS or now - s["created"] > MAX_SECONDS:
            self.end(token)
            return False
        if now - s["seen"] > 60:
            s["seen"] = now
            self.store.set_kv(self._key(token), json.dumps(s))
        return True

    def end(self, token: str | None) -> None:
        if token:
            self.store.x("DELETE FROM kv WHERE key=?", (self._key(token),))
