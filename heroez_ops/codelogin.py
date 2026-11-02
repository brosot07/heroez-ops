"""One-time login codes sent by the Heroez Telegram bot, and the browser sessions they open.

Used when the web app is reached directly (no Cloudflare Access in front):
* team pages: a person types their work email → the bot sends a 6-digit code to that person's
  Telegram → the code opens a session
* settings page: owner password, then a code sent to the owner's Telegram

Codes: 10 minutes, one use, 5 wrong tries, at most 1 per minute and 5 per hour per person.
Only hashes of codes and session tokens are stored. Sessions end when the browser closes,
after 2 hours idle, or after 12 hours.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from .db import Store

CODE_TTL, MAX_TRIES = 10 * 60, 5
PER_MINUTE, PER_HOUR = 1, 5
IDLE_SECONDS, MAX_SECONDS = 2 * 3600, 12 * 3600


def _h(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()


class CodeLogin:
    def __init__(self, store: Store, scope: str):
        self.store, self.scope = store, scope            # scope: "team" | "owner"

    # ---------------------------------------------------------------- codes
    def can_send(self, who: str, now: float | None = None) -> bool:
        now = now or time.time()
        sent = [t for t in json.loads(self.store.get_kv(f"{self.scope}_sent:{_h(who)}", "[]")) if t > now - 3600]
        return len([t for t in sent if t > now - 60]) < PER_MINUTE and len(sent) < PER_HOUR

    def new_code(self, who: str, now: float | None = None) -> str | None:
        """A fresh code for this person, or None when rate-limited."""
        now = now or time.time()
        if not self.can_send(who, now):
            return None
        code = f"{secrets.randbelow(1_000_000):06d}"
        sent = [t for t in json.loads(self.store.get_kv(f"{self.scope}_sent:{_h(who)}", "[]")) if t > now - 3600] + [now]
        self.store.set_kv(f"{self.scope}_sent:{_h(who)}", json.dumps(sent))
        self.store.set_kv(f"{self.scope}_code:{_h(who)}",
                          json.dumps({"hash": _h(who, code), "exp": now + CODE_TTL, "tries": 0}))
        return code

    def check_code(self, who: str, code: str, now: float | None = None) -> bool:
        now = now or time.time()
        key = f"{self.scope}_code:{_h(who)}"
        raw = self.store.get_kv(key)
        if not raw:
            return False
        rec = json.loads(raw)
        if rec["exp"] < now or rec["tries"] >= MAX_TRIES:
            self.store.x("DELETE FROM kv WHERE key=?", (key,))
            return False
        if hmac.compare_digest(rec["hash"], _h(who, "".join(ch for ch in code if ch.isdigit()))):
            self.store.x("DELETE FROM kv WHERE key=?", (key,))       # one use
            return True
        rec["tries"] += 1
        self.store.set_kv(key, json.dumps(rec))
        return False

    # ---------------------------------------------------------------- sessions
    def new_session(self, who: str) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        self.store.set_kv(f"{self.scope}_session:{_h(token)}", json.dumps({"who": who, "created": now, "seen": now}))
        return token

    def session(self, token: str | None) -> str | None:
        if not token:
            return None
        key = f"{self.scope}_session:{_h(token)}"
        raw = self.store.get_kv(key)
        if not raw:
            return None
        s, now = json.loads(raw), int(time.time())
        if now - s["seen"] > IDLE_SECONDS or now - s["created"] > MAX_SECONDS:
            self.store.x("DELETE FROM kv WHERE key=?", (key,))
            return None
        if now - s["seen"] > 60:
            s["seen"] = now
            self.store.set_kv(key, json.dumps(s))
        return s["who"]

    def end(self, token: str | None) -> None:
        if token:
            self.store.x("DELETE FROM kv WHERE key=?", (f"{self.scope}_session:{_h(token)}",))

    def end_all(self, who: str) -> None:
        for r in self.store.q("SELECT key, value FROM kv WHERE key LIKE ?", (f"{self.scope}_session:%",)):
            if json.loads(r["value"]).get("who") == who:
                self.store.x("DELETE FROM kv WHERE key=?", (r["key"],))
