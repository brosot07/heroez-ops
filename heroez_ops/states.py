"""Conversation state engine (rules only; an LLM refiner can be plugged in later).

Every conversation has exactly one state. Every change is stored with a reason.
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .db import Store
from .phone import normalize
from .sources.base import ContactEvent, Event, MessageEvent
from .textrules import classify_inbound, is_emoji_only

log = logging.getLogger(__name__)

AWAITING_US = "AWAITING_US"
AWAITING_THEM = "AWAITING_THEM"
PROMISE_PENDING = "PROMISE_PENDING"
CLOSED = "CLOSED"
POSSIBLY_REPLIED = "POSSIBLY_REPLIED"

# Escalation step names (also used in fired_steps)
ALL_STEPS = ("nudge", "group", "management", "urgent", "overdue")
NO_REPEAT = "no_repeat"   # in fired_steps: backfilled chat, no hourly repeats


@dataclass
class Change:
    wa_id: str
    from_state: str | None
    to_state: str
    reason: str


class Engine:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.roster: dict[str, dict] = {}
        self.ai_enabled = False   # set by Service when a Claude API key is configured
        self.load_roster()

    # -- roster ---------------------------------------------------------
    def load_roster(self) -> list[str]:
        """Load talent roster CSV (name,phone,handler). Returns plain-language problems."""
        problems: list[str] = []
        path = self.cfg.talent_roster_file
        roster: dict[str, dict] = {}
        if path and Path(path).exists():
            keys = {a.key for a in self.cfg.admins}
            with open(path, newline="", encoding="utf-8-sig") as f:
                for i, row in enumerate(csv.DictReader(f), start=2):
                    wa = normalize(row.get("phone", ""), local=True)
                    if not wa:
                        problems.append(f"Roster baris {i}: nomor HP kosong/tidak valid ({row.get('name', '')})")
                        continue
                    handler = (row.get("handler") or "").strip() or None
                    if handler and handler not in keys:
                        problems.append(f"Roster baris {i}: handler '{handler}' bukan admin yang terdaftar")
                        handler = None
                    roster[wa] = {"name": (row.get("name") or "").strip(), "handler": handler}
        self.roster = roster
        for wa, r in roster.items():
            self._ensure_contact(wa, r["name"], 0)
            self.store.x("UPDATE contacts SET kind='talent', handler=COALESCE(?, handler) WHERE wa_id=?", (r["handler"], wa))
        return problems

    # -- helpers --------------------------------------------------------
    def _ensure_contact(self, wa_id: str, name: str | None, ts: int) -> None:
        self.store.x(
            "INSERT INTO contacts(wa_id,name,kind,created_at,last_seen_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(wa_id) DO UPDATE SET name=COALESCE(contacts.name, excluded.name), "
            "last_seen_at=MAX(COALESCE(contacts.last_seen_at,0), excluded.last_seen_at)",
            (wa_id, name, "talent" if wa_id in self.roster else "new", ts, ts),
        )

    def conversation(self, wa_id: str):
        return self.store.one("SELECT * FROM conversations WHERE wa_id=?", (wa_id,))

    def _set_state(self, wa_id: str, new: str, reason: str, at: int, msg_id: str | None = None, **fields) -> Change | None:
        conv = self.conversation(wa_id)
        old = conv["state"] if conv else None
        cols = dict(fields)
        if conv is None:
            cols.setdefault("fired_steps", "[]")
            self.store.x(
                "INSERT INTO conversations(wa_id,state,state_reason,state_since,updated_at) VALUES(?,?,?,?,?)",
                (wa_id, new, reason, at, at),
            )
        if old == AWAITING_US and new != AWAITING_US and conv["waiting_since"]:
            # One finished wait = one response-time sample for the digest.
            self.store.x("INSERT INTO waits(wa_id,started_at,ended_at,assignee,ended_by) VALUES(?,?,?,?,?)",
                         (wa_id, conv["waiting_since"], at, conv["assignee"], new))
        if old != new:
            cols.update(state=new, state_reason=reason, state_since=at)
            self.store.x(
                "INSERT INTO state_changes(wa_id,from_state,to_state,reason,message_id,at) VALUES(?,?,?,?,?,?)",
                (wa_id, old, new, reason, msg_id, at),
            )
        cols["updated_at"] = at
        sets = ", ".join(f"{k}=?" for k in cols)
        self.store.x(f"UPDATE conversations SET {sets} WHERE wa_id=?", (*cols.values(), wa_id))
        return Change(wa_id, old, new, reason) if old != new else None

    def _assign(self, wa_id: str, at: int) -> None:
        conv = self.conversation(wa_id)
        if conv and conv["assignee"]:
            return
        contact = self.store.one("SELECT kind, handler FROM contacts WHERE wa_id=?", (wa_id,))
        if contact and contact["handler"]:
            how = "roster" if contact["kind"] == "talent" else "sticky"
            assignee = contact["handler"]
        else:
            keys = [a.key for a in self.cfg.admins]
            n = int(self.store.get_kv("round_robin", "0"))
            assignee, how = keys[n % len(keys)], "round_robin"
            self.store.set_kv("round_robin", str(n + 1))
        self.store.x("UPDATE conversations SET assignee=?, assigned_how=? WHERE wa_id=?", (assignee, how, wa_id))
        self.store.audit("system", "assign", wa_id, {"assignee": assignee, "how": how}, at)

    # -- event handling -------------------------------------------------
    def handle(self, ev: Event) -> Change | None:
        """Store an event and update state. Idempotent: a repeated message id is ignored."""
        if isinstance(ev, ContactEvent):
            if not ev.removed:
                self._ensure_contact(ev.wa_id, ev.name, 0)
                if ev.name:
                    self.store.x("UPDATE contacts SET name=? WHERE wa_id=?", (ev.name, ev.wa_id))
            return None

        with self.store.tx():
            dup = self.store.one("SELECT 1 FROM messages WHERE id=?", (ev.msg_id,))
            if dup:
                return None
            self._ensure_contact(ev.wa_id, ev.contact_name, ev.ts)
            self.store.x(
                "INSERT INTO messages(id,wa_id,direction,source,ts,type,text,raw,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (ev.msg_id, ev.wa_id, ev.direction, ev.source, ev.ts, ev.type, ev.text,
                 json.dumps(ev.raw, ensure_ascii=False), ev.ts),
            )
            change = self._outbound(ev) if ev.direction == "out" else self._inbound(ev)
        return change

    def _outbound(self, ev: MessageEvent) -> Change | None:
        conv = self.conversation(ev.wa_id)
        self.store.x("UPDATE contacts SET kind=CASE WHEN kind='new' THEN 'existing_client' ELSE kind END WHERE wa_id=?", (ev.wa_id,))
        last_out = max(ev.ts, conv["last_outbound_at"] or 0) if conv else ev.ts
        if conv and conv["waiting_since"] and ev.ts < conv["waiting_since"]:
            # Late echo for a reply sent before the message we are still waiting on.
            self.store.x("UPDATE conversations SET last_outbound_at=? WHERE wa_id=?", (last_out, ev.wa_id))
            return None
        if conv and conv["state"] in (AWAITING_THEM, CLOSED) and conv["state_since"] > ev.ts:
            return None
        return self._set_state(
            ev.wa_id, AWAITING_THEM, f"admin replied ({ev.source})", ev.ts, ev.msg_id,
            waiting_since=None, last_outbound_at=last_out, fired_steps="[]", snoozed_until=None,
            ai_pending=0, ai_msg_id=None,
        )

    def _inbound(self, ev: MessageEvent) -> Change | None:
        conv = self.conversation(ev.wa_id)
        last_in = max(ev.ts, (conv["last_inbound_at"] or 0) if conv else 0)
        if conv and conv["last_outbound_at"] and ev.ts < conv["last_outbound_at"]:
            # Arrived out of order: we already replied after it.
            self.store.x("UPDATE conversations SET last_inbound_at=? WHERE wa_id=?", (last_in, ev.wa_id))
            return None

        kind = classify_inbound(ev.type, ev.text)
        prev = conv["state"] if conv else None
        history = ev.source == "history"
        # The text rules give an immediate state; the AI re-checks every message except
        # stickers/reactions/emoji and may override it before any alert is due.
        ai = {}
        if self.ai_enabled and not (kind == "ack" and (ev.type in ("sticker", "reaction") or is_emoji_only(ev.text))):
            ai = {"ai_pending": 1, "ai_msg_id": ev.msg_id}

        if kind == "ack":
            if prev == AWAITING_US:
                return self._set_state(ev.wa_id, POSSIBLY_REPLIED, "client acknowledged while we looked unanswered (reply probably sent from a device without echoes)",
                                       ev.ts, ev.msg_id, last_inbound_at=last_in, **ai)
            if prev == POSSIBLY_REPLIED:
                return self._set_state(ev.wa_id, POSSIBLY_REPLIED, "ack", ev.ts, ev.msg_id, last_inbound_at=last_in, **ai)
            return self._set_state(ev.wa_id, CLOSED, "last message needs no reply", ev.ts, ev.msg_id, last_inbound_at=last_in, **ai)

        if kind == "thanks":
            if prev in (AWAITING_THEM, CLOSED):
                return self._set_state(ev.wa_id, CLOSED, "client said thanks", ev.ts, ev.msg_id, last_inbound_at=last_in, **ai)
            return self._set_state(ev.wa_id, POSSIBLY_REPLIED, "client thanked us for something we never saw sent",
                                   ev.ts, ev.msg_id, last_inbound_at=last_in, **ai)

        # needs_reply
        waiting_since = conv["waiting_since"] if conv and prev == AWAITING_US and conv["waiting_since"] else ev.ts
        fired = conv["fired_steps"] if conv and prev == AWAITING_US else "[]"
        if history:
            # Backfilled chats show up in /semua and the morning list, but must not
            # trigger a flood of escalation alerts on onboarding day.
            fired = json.dumps(list(ALL_STEPS) + [NO_REPEAT])
        change = self._set_state(ev.wa_id, AWAITING_US, "client message needs a reply", ev.ts, ev.msg_id,
                                 waiting_since=waiting_since, last_inbound_at=last_in, fired_steps=fired, **ai)
        self._assign(ev.wa_id, ev.ts)
        return change

    # -- AI verdicts ----------------------------------------------------
    def apply_verdict(self, wa_id: str, msg_id: str, v, min_confidence: float, at: int) -> Change | None:
        """Apply an AI verdict about message msg_id, unless something newer happened since."""
        conv = self.conversation(wa_id)
        if not conv or conv["ai_msg_id"] != msg_id:
            return None  # a newer message or a reply arrived; that one gets its own check
        msg = self.store.one("SELECT ts, source FROM messages WHERE id=?", (msg_id,))
        self.store.x("UPDATE conversations SET ai_pending=0, ai_kind=?, ai_summary=?, ai_confidence=?, priority=? WHERE wa_id=?",
                     (v.kind, v.summary, v.confidence, v.priority if v.needs_reply else conv["priority"], wa_id))
        self.store.audit("ai", "verdict", wa_id, {"msg": msg_id, "kind": v.kind, "needs_reply": v.needs_reply,
                                                   "confidence": v.confidence, "priority": v.priority}, at)
        reason = f"AI: {v.kind} ({v.confidence:.2f}) {v.summary}"
        if v.needs_reply and conv["state"] != AWAITING_US:
            fired = json.dumps(list(ALL_STEPS) + [NO_REPEAT]) if msg and msg["source"] == "history" else "[]"
            change = self._set_state(wa_id, AWAITING_US, reason, at, msg_id,
                                     waiting_since=msg["ts"] if msg else at, fired_steps=fired)
            self._assign(wa_id, at)
            return change
        if not v.needs_reply and v.confidence >= min_confidence and conv["state"] in (AWAITING_US, POSSIBLY_REPLIED):
            return self._set_state(wa_id, CLOSED, reason, at, msg_id, waiting_since=None, fired_steps="[]")
        return None

    def ai_failed(self, wa_id: str) -> None:
        """Give up on the AI for this message; the text-rule state stands."""
        self.store.x("UPDATE conversations SET ai_pending=0 WHERE wa_id=?", (wa_id,))

    # -- admin actions (Telegram buttons) ------------------------------
    def claim(self, wa_id: str, admin: str, at: int) -> None:
        self.store.x("UPDATE conversations SET assignee=?, assigned_how='claim', updated_at=? WHERE wa_id=?", (admin, at, wa_id))
        self.store.x("UPDATE contacts SET handler=? WHERE wa_id=? AND kind!='talent'", (admin, wa_id))
        self.store.audit(admin, "claim", wa_id, None, at)

    def mark_replied(self, wa_id: str, admin: str, at: int) -> None:
        conv = self.conversation(wa_id)
        if not conv:
            return
        self.store.x("INSERT INTO feedback(wa_id,kind,admin,state_before,at) VALUES(?,?,?,?,?)",
                     (wa_id, "already_replied", admin, conv["state"], at))
        self._set_state(wa_id, AWAITING_THEM, f"marked replied by {admin}", at,
                        waiting_since=None, fired_steps="[]", snoozed_until=None, last_outbound_at=at,
                        ai_pending=0, ai_msg_id=None)
        self.store.audit(admin, "mark_replied", wa_id, None, at)

    def mark_not_ours(self, wa_id: str, admin: str, at: int) -> None:
        conv = self.conversation(wa_id)
        if not conv:
            return
        self.store.x("INSERT INTO feedback(wa_id,kind,admin,state_before,at) VALUES(?,?,?,?,?)",
                     (wa_id, "not_ours", admin, conv["state"], at))
        self._set_state(wa_id, CLOSED, f"no reply needed (per {admin})", at,
                        waiting_since=None, fired_steps="[]", snoozed_until=None,
                        ai_pending=0, ai_msg_id=None)
        self.store.audit(admin, "mark_not_ours", wa_id, None, at)

    def snooze(self, wa_id: str, admin: str, at: int) -> int:
        until = at + self.cfg.escalation.snooze_minutes * 60
        self.store.x("UPDATE conversations SET snoozed_until=?, updated_at=? WHERE wa_id=?", (until, at, wa_id))
        self.store.audit(admin, "snooze", wa_id, {"until": until}, at)
        return until

    def set_priority(self, wa_id: str, priority: str, at: int) -> None:
        self.store.x("UPDATE conversations SET priority=?, updated_at=? WHERE wa_id=?", (priority, at, wa_id))
