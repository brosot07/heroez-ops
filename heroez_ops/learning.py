"""How the app learns from the admins.

* Every admin reply (echo) to a client question becomes a candidate example. A supervisor
  approves the good ones; approved examples are shown to the AI when it drafts, so drafts
  sound like Heroez.
* When a draft existed for the message the admin answered, the draft and the real reply
  are compared: used as-is, edited, or ignored. The digest reports the weekly rate.
* Feedback buttons (👍/👎, Sudah dibalas, Bukan spam) are stored in `feedback` and reported.

The model itself never changes; everything learned lives in the database and can be edited.
"""
from __future__ import annotations

import difflib
import time

from .db import Store
from .sources.base import MessageEvent
from .textrules import classify_inbound, redact

MAX_EXAMPLES_IN_PROMPT = 10
MAX_CANDIDATES = 200
USED_AS_IS, EDITED = 0.9, 0.5


class Learning:
    def __init__(self, store: Store):
        self.store = store

    # ---------------------------------------------------------------- hooks
    def on_admin_reply(self, ev: MessageEvent) -> None:
        """Called after an admin reply (echo or API send) was stored."""
        if ev.direction != "out" or ev.source not in ("echo", "api") or not (ev.text or "").strip():
            return
        last_in = self.store.one("SELECT id, text, type FROM messages WHERE wa_id=? AND direction='in' AND ts<=? "
                                 "ORDER BY ts DESC LIMIT 1", (ev.wa_id, ev.ts))
        if not last_in or not (last_in["text"] or "").strip():
            return
        if classify_inbound(last_in["type"], last_in["text"]) == "ack":
            return                                     # "ok makasih" → "sama-sama" teaches nothing
        conv = self.store.one("SELECT draft_text, draft_msg_id, draft_can_answer FROM conversations WHERE wa_id=?", (ev.wa_id,))
        client_text, reply = redact(last_in["text"].strip())[:600], redact(ev.text.strip())[:800]
        self.store.x("INSERT INTO reply_examples(wa_id,client_text,admin_reply,status,created_at) VALUES(?,?,?,'candidate',?)",
                     (ev.wa_id, client_text, reply, ev.ts))
        self.store.x("DELETE FROM reply_examples WHERE status='candidate' AND id NOT IN "
                     "(SELECT id FROM reply_examples WHERE status='candidate' ORDER BY id DESC LIMIT ?)", (MAX_CANDIDATES,))
        if conv and conv["draft_text"] and conv["draft_msg_id"] == last_in["id"] and conv["draft_can_answer"]:
            ratio = difflib.SequenceMatcher(None, conv["draft_text"].strip().lower(), ev.text.strip().lower()).ratio()
            outcome = "used" if ratio >= USED_AS_IS else "edited" if ratio >= EDITED else "ignored"
            self.store.x("INSERT INTO draft_outcomes(wa_id,msg_id,draft,actual,similarity,outcome,at) VALUES(?,?,?,?,?,?,?)",
                         (ev.wa_id, last_in["id"], redact(conv["draft_text"])[:800], reply, round(ratio, 3), outcome, ev.ts))

    # ---------------------------------------------------------------- examples
    def candidates(self, limit: int = 20):
        return self.store.q("SELECT * FROM reply_examples WHERE status='candidate' ORDER BY id DESC LIMIT ?", (limit,))

    def approved(self, limit: int = MAX_EXAMPLES_IN_PROMPT):
        return self.store.q("SELECT * FROM reply_examples WHERE status='approved' ORDER BY approved_at DESC LIMIT ?", (limit,))

    def decide(self, example_id: int, approve: bool, who: str, client_text: str | None = None,
               admin_reply: str | None = None) -> None:
        if approve:
            self.store.x("UPDATE reply_examples SET status='approved', approved_at=?, approved_by=?, "
                         "client_text=COALESCE(?, client_text), admin_reply=COALESCE(?, admin_reply) WHERE id=?",
                         (int(time.time()), who, (client_text or "").strip() or None, (admin_reply or "").strip() or None, example_id))
        else:
            self.store.x("DELETE FROM reply_examples WHERE id=?", (example_id,))
        self.store.audit(who, "example_approve" if approve else "example_reject", None, {"id": example_id})

    def examples_for_prompt(self) -> str:
        rows = self.approved()
        if not rows:
            return ""
        parts = [f"KLIEN: {r['client_text']}\nHEROEZ: {r['admin_reply']}" for r in reversed(rows)]
        return ("## Contoh balasan admin Heroez (tiru gaya dan panjangnya; faktanya bisa berbeda)\n\n"
                + "\n\n".join(parts))

    # ---------------------------------------------------------------- stats
    def draft_stats(self, since: int, until: int) -> dict:
        rows = self.store.q("SELECT outcome, COUNT(*) n FROM draft_outcomes WHERE at>=? AND at<? GROUP BY outcome", (since, until))
        d = {r["outcome"]: r["n"] for r in rows}
        n = sum(d.values())
        return {"n": n, "used": d.get("used", 0), "edited": d.get("edited", 0), "ignored": d.get("ignored", 0),
                "used_pct": (100 * d.get("used", 0) // n) if n else 0}

    def prune(self, before: int) -> None:
        self.store.x("DELETE FROM reply_examples WHERE status='candidate' AND created_at<?", (before,))
        self.store.x("DELETE FROM draft_outcomes WHERE at<?", (before,))
