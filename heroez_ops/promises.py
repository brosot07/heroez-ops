"""Promise tracker: "besok saya kirim proposalnya" becomes a reminder for the admin who said it.

Detection is rule-based (no AI cost): an admin reply that contains a follow-up verb and a time
phrase in Bahasa Indonesia. When the time comes the admin gets one Telegram nudge with
✅ Sudah / ⏰ Nanti buttons; two business hours later the supervisors hear about it once.
Any later admin reply to the same client closes the promise (they followed up).
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import re
import time

from .alerts.base import Button
from .config import Config
from .db import Store
from .sources.base import MessageEvent
from .textrules import redact

log = logging.getLogger(__name__)

VERBS = r"(?:kirim|kirimkan|kirimin|share|kabari|kabarin|kabarkan|info|infoin|infokan|update|updatenya|cek|cekin|" \
        r"konfirmasi|konfirm|follow ?up|tanya|tanyakan|tanyain|hubungi|balas|jawab|siapkan|buatkan|bikinin|revisi|" \
        r"proses|urus|susun|send|revert|get back|confirm|check)"
DAYS = {"senin": 0, "selasa": 1, "rabu": 2, "kamis": 3, "jumat": 4, "jum'at": 4, "sabtu": 5, "minggu": 6,
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
TIME_RE = re.compile(
    r"\b(?P<when>besok|lusa|hari ini|nanti (?:sore|siang|malam|pagi)|sore ini|siang ini|malam ini|pagi ini|"
    r"minggu depan|next week|tomorrow|today|tonight|secepatnya|segera|asap|"
    r"(?:hari )?(?:senin|selasa|rabu|kamis|jum'?at|sabtu|minggu)(?! depan)|"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"(?:jam|pukul) ?(?P<hour>\d{1,2})(?:[.:](?P<min>\d{2}))?(?: ?(?P<ampm>pagi|siang|sore|malam|am|pm))?)\b", re.I)
PROMISE_RE = re.compile(rf"\b(?:saya|aku|kami|kita|nanti|akan|bakal|coba|segera|we|i)\b.{{0,40}}\b{VERBS}\b|\b{VERBS}\b.{{0,40}}\b(?:ya|yaa|kak|nanti|dulu|segera)\b", re.I | re.S)
NOT_PROMISE_RE = re.compile(r"\?\s*$|\b(?:tolong|mohon|bisa (?:kirim|share|info)|boleh (?:kirim|share|info))\b", re.I)

SUPERVISOR_AFTER_MIN = 120


def detect(text: str, now_local: dt.datetime) -> tuple[str, dt.datetime] | None:
    """Return (promise text, due datetime in local tz) if the admin promised something at a time."""
    t = (text or "").strip()
    if not t or len(t) > 1200:
        return None
    # Look sentence by sentence so a question in the same message doesn't disqualify the promise.
    for sent in re.split(r"(?<=[.!?\n])\s+", t):
        m = TIME_RE.search(sent)
        if not m or not PROMISE_RE.search(sent) or NOT_PROMISE_RE.search(sent):
            continue
        due = _due(m, now_local)
        if due:
            return redact(sent.strip())[:200], due
    return None


def _due(m: re.Match, now: dt.datetime) -> dt.datetime | None:
    when = m.group("when").lower().replace("hari ", "")
    day10 = lambda d: now.replace(hour=10, minute=0, second=0, microsecond=0) + dt.timedelta(days=d)  # noqa: E731
    if m.group("hour"):
        h, mi = int(m.group("hour")), int(m.group("min") or 0)
        ampm = (m.group("ampm") or "").lower()
        if ampm in ("sore", "malam", "pm") and h < 12:
            h += 12
        if ampm == "siang" and h < 11:
            h += 12
        if not 0 <= h <= 23:
            return None
        due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        return due if due > now else due + dt.timedelta(days=1)
    if when in ("besok", "tomorrow"):
        return day10(1)
    if when == "lusa":
        return day10(2)
    if when in ("hari ini", "today", "siang ini", "nanti siang"):
        return _later_today(now, 13)
    if when in ("sore ini", "nanti sore"):
        return _later_today(now, 16)
    if when in ("malam ini", "nanti malam", "tonight"):
        return _later_today(now, 19)
    if when in ("pagi ini", "nanti pagi"):
        return _later_today(now, 10) if now.hour < 10 else day10(1)
    if when in ("minggu depan", "next week"):
        return day10((7 - now.weekday()) % 7 or 7)
    if when in ("secepatnya", "segera", "asap"):
        return now + dt.timedelta(hours=4)
    if when.replace("'", "") in DAYS or when in DAYS:
        wd = DAYS.get(when, DAYS.get(when.replace("'", "")))
        return day10((wd - now.weekday()) % 7 or 7)
    return None


def _later_today(now: dt.datetime, hour: int) -> dt.datetime:
    due = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return due if due > now + dt.timedelta(minutes=30) else now + dt.timedelta(hours=2)


class Promises:
    def __init__(self, cfg: Config, store: Store, clock, channel):
        self.cfg, self.store, self.clock, self.channel = cfg, store, clock, channel

    # ---------------------------------------------------------------- hooks
    def on_admin_reply(self, ev: MessageEvent) -> None:
        if ev.direction != "out" or ev.source not in ("echo", "api"):
            return
        # The admin followed up: earlier open promises to this client are kept.
        self.store.x("UPDATE promises SET status='done', done_at=?, done_by='reply' WHERE wa_id=? AND status='open' AND created_at<?",
                     (ev.ts, ev.wa_id, ev.ts - 60))
        local = dt.datetime.fromtimestamp(ev.ts, self.cfg.tz)
        found = detect(ev.text or "", local)
        if not found:
            return
        text, due = found
        conv = self.store.one("SELECT assignee FROM conversations WHERE wa_id=?", (ev.wa_id,))
        admin = conv["assignee"] if conv and conv["assignee"] and self.cfg.admin(conv["assignee"]) else None
        self.store.x("INSERT INTO promises(wa_id,admin,text,due_at,status,created_at) VALUES(?,?,?,?,'open',?)",
                     (ev.wa_id, admin, text, int(due.timestamp()), ev.ts))
        self.store.audit("system", "promise", ev.wa_id, {"due": due.isoformat(), "admin": admin}, ev.ts)

    def mark_done(self, promise_id: int, who: str, at: int) -> bool:
        return self.store.x("UPDATE promises SET status='done', done_at=?, done_by=? WHERE id=? AND status='open'",
                            (at, who, promise_id)).rowcount > 0

    def snooze(self, promise_id: int, at: int, hours: int = 2) -> bool:
        return self.store.x("UPDATE promises SET due_at=?, reminded_at=NULL WHERE id=? AND status='open'",
                            (at + hours * 3600, promise_id)).rowcount > 0

    # ---------------------------------------------------------------- reminders
    def remind(self, now: dt.datetime) -> int:
        """Nudge the admin at the due time (notification hours only), supervisors 2 business hours later."""
        if not self.clock.is_open(now):
            return 0
        now_ts, sent = int(now.timestamp()), 0
        for p in self.store.q("SELECT * FROM promises WHERE status='open' AND due_at<=? AND reminded_at IS NULL", (now_ts,)):
            self.store.x("UPDATE promises SET reminded_at=? WHERE id=?", (now_ts, p["id"]))
            sent += self._send(p, self._admin_ids(p) or self._spv_ids(p), now)
        late = self.clock.add_business_minutes(now, -SUPERVISOR_AFTER_MIN)
        for p in self.store.q("SELECT * FROM promises WHERE status='open' AND reminded_at IS NOT NULL AND reminded_at<=? "
                              "AND escalated_at IS NULL", (int(late.timestamp()),)):
            self.store.x("UPDATE promises SET escalated_at=? WHERE id=?", (now_ts, p["id"]))
            sent += self._send(p, self._spv_ids(p), now, late=True)
        return sent

    def _admin_ids(self, p) -> list[int]:
        a = self.cfg.admin(p["admin"]) if p["admin"] else None
        return [a.telegram_id] if a and a.telegram_id and not self.cfg.review else []

    def _spv_ids(self, p) -> list[int]:
        spvs = self.cfg.supervisors_of(p["admin"]) if p["admin"] else self.cfg.supervisors
        return [s.telegram_id for s in spvs if s.telegram_id]

    def _send(self, p, chat_ids: list[int], now: dt.datetime, late: bool = False) -> int:
        from .phone import display
        contact = self.store.one("SELECT name FROM contacts WHERE wa_id=?", (p["wa_id"],))
        name = html.escape((contact["name"] if contact else "") or display(p["wa_id"]))
        who = html.escape(self.cfg.person_name(p["admin"])) if p["admin"] else "admin"
        due = dt.datetime.fromtimestamp(p["due_at"], self.cfg.tz).strftime("%a %d/%m %H:%M")
        head = (f"⚠️ Janji {who} ke <b>{name}</b> belum ditandai selesai (jatuh tempo {due}):" if late
                else f"⏰ Janji ke <b>{name}</b> jatuh tempo ({due}):")
        text = f"{head}\n“{html.escape(p['text'])}”\n<a href=\"https://wa.me/{p['wa_id']}\">Buka chat</a>"
        buttons = [[Button("✅ Sudah", f"pdone:{p['wa_id']}:{p['id']}"), Button("⏰ 2 jam lagi", f"psnooze:{p['wa_id']}:{p['id']}")]]
        for cid in dict.fromkeys(chat_ids):
            self.channel.send(cid, text, buttons)
        return len(set(chat_ids))

    # ---------------------------------------------------------------- listing
    def open_text(self, admin_key: str | None, now: dt.datetime) -> str:
        from .phone import display
        if admin_key:
            rows = self.store.q("SELECT * FROM promises WHERE status='open' AND admin=? ORDER BY due_at", (admin_key,))
        else:
            rows = self.store.q("SELECT * FROM promises WHERE status='open' ORDER BY due_at")
        if not rows:
            return "🟢 Tidak ada janji yang terbuka."
        out = ["<b>Janji yang belum ditandai selesai</b>"]
        for p in rows:
            c = self.store.one("SELECT name FROM contacts WHERE wa_id=?", (p["wa_id"],))
            name = html.escape((c["name"] if c else "") or display(p["wa_id"]))
            due = dt.datetime.fromtimestamp(p["due_at"], self.cfg.tz)
            flag = "🔴" if due <= now else "🕒"
            who = f" ({html.escape(self.cfg.person_name(p['admin']))})" if not admin_key and p["admin"] else ""
            out.append(f"{flag} {due:%a %d/%m %H:%M} · <b>{name}</b>{who}: “{html.escape(p['text'])}” "
                       f"<a href=\"https://wa.me/{p['wa_id']}\">Buka</a>")
        return "\n".join(out)

    def stats(self, since: int, until: int) -> dict:
        kept = self.store.one("SELECT COUNT(*) n FROM promises WHERE status='done' AND done_at>=? AND done_at<? AND done_at<=due_at+7200",
                              (since, until))["n"]
        late = self.store.one("SELECT COUNT(*) n FROM promises WHERE status='done' AND done_at>=? AND done_at<? AND done_at>due_at+7200",
                              (since, until))["n"]
        open_late = self.store.one("SELECT COUNT(*) n FROM promises WHERE status='open' AND due_at<?", (until,))["n"]
        return {"kept": kept, "late": late, "open_late": open_late}

    def prune(self, before: int) -> None:
        self.store.x("DELETE FROM promises WHERE created_at<?", (before,))
        self.store.x("UPDATE promises SET status='expired' WHERE status='open' AND due_at<?", (int(time.time()) - 14 * 86400,))
