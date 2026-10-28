"""Turns due escalation steps into batched Telegram messages (Bahasa Indonesia).

Rules: one message per recipient per N minutes (config), items grouped into a
list, most urgent first. Alerts carry a short snippet, never a full chat dump.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import re
import time

from .alerts.base import AlertChannel, Button
from .bizhours import BusinessClock
from .config import Config
from .db import Store
from .escalation import Escalator
from .drafts import parse_draft
from .links import alert_line as link_alert
from .links import defang
from .phone import display
from .textrules import redact  # noqa: F401  (re-exported for callers)
from .states import AWAITING_US, POSSIBLY_REPLIED

log = logging.getLogger(__name__)

STEP_RANK = {"overdue": 5, "urgent": 4, "management": 3, "group": 2, "reminder": 1, "nudge": 1}
STEP_ICON = {"overdue": "🔴", "urgent": "🔴", "management": "🔴", "group": "🟠", "reminder": "🟠", "nudge": "🟠"}
STEP_LABEL = {
    "nudge": "Belum dibalas",
    "reminder": "Masih belum dibalas",
    "group": "Belum dibalas >2 jam kerja",
    "management": "Lead P1 belum dibalas",
    "urgent": "URGENT: hampir 24 jam belum dibalas",
    "overdue": "Lewat 24 jam belum dibalas",
}
KIND_LABEL = {"talent": "Talent", "existing_client": "Klien lama", "new": "Kontak baru"}

def human_duration(minutes: float) -> str:
    minutes = int(minutes)
    if minutes < 60:
        return f"{minutes} menit"
    h, m = divmod(minutes, 60)
    if h < 24:
        return f"{h} jam" + (f" {m} menit" if m else "")
    d, h = divmod(h, 24)
    return f"{d} hari" + (f" {h} jam" if h else "")


class Notifier:
    def __init__(self, cfg: Config, store: Store, clock: BusinessClock, channel: AlertChannel):
        self.cfg, self.store, self.clock, self.channel = cfg, store, clock, channel
        self.escalator = Escalator(cfg, store, clock)
        self.before_alert = None   # callable(wa_id): e.g. prepare an AI reply suggestion

    # -- recipients -----------------------------------------------------
    def chat_id(self, recipient: str) -> int:
        kind, _, key = recipient.partition(":")
        if kind == "admin":
            a = self.cfg.admin(key)
            return a.telegram_id if a else 0
        if kind == "spv":
            s = self.cfg.supervisor(key)
            return s.telegram_id if s else 0
        if key == "admin":
            return self.cfg.admin_group_id
        if key == "management":
            return self.cfg.management_group_id
        return 0

    # -- main loop step -------------------------------------------------
    def tick(self, now: dt.datetime) -> None:
        """Called every minute: queue due steps, flush batches, morning list."""
        now_ts = int(now.timestamp())
        for d in self.escalator.due(now):
            if self.before_alert and d.step in ("nudge", "reminder", "group", "urgent"):
                try:
                    self.before_alert(d.wa_id)
                except Exception:
                    log.exception("before_alert failed for %s", d.wa_id)
            for cid in dict.fromkeys(self.chat_id(r) for r in d.recipients):  # dedupe, keep order
                if cid:
                    self.store.x("INSERT INTO outbox(chat_id,wa_id,step,created_at) VALUES(?,?,?,?)", (cid, d.wa_id, d.step, now_ts))
            missing = [r for r in d.recipients if not self.chat_id(r) and not r.startswith("group:")]
            if missing:
                log.warning("no Telegram ID set for %s; they miss alert %s/%s", missing, d.wa_id, d.step)
            self.escalator.mark_fired(d.wa_id, d.key or d.step)
            self.store.audit("system", f"escalate:{d.step}", d.wa_id, {"recipients": list(d.recipients)}, now_ts)
        self.flush(now)
        self.morning_list(now)

    def flush(self, now: dt.datetime) -> None:
        now_ts = int(now.timestamp())
        weekend = self.clock.is_quiet_day(now.astimezone(self.cfg.tz).date())
        gap = (self.cfg.weekend_min_minutes_between_messages if weekend
               else self.cfg.min_minutes_between_messages) * 60
        for row in self.store.q("SELECT DISTINCT chat_id FROM outbox WHERE sent_at IS NULL"):
            cid = row["chat_id"]
            last = self.store.one("SELECT MAX(sent_at) AS t FROM sent_batches WHERE chat_id=?", (cid,))
            if last and last["t"] and now_ts - last["t"] < gap:
                continue
            pending = self.store.q("SELECT * FROM outbox WHERE chat_id=? AND sent_at IS NULL", (cid,))
            if not self.clock.is_open(now):
                # After hours only urgent items go out; routine ones wait for the 08:30 morning list.
                routine = [p["id"] for p in pending if p["step"] not in ("urgent", "overdue")]
                if routine:
                    self.store.x(f"UPDATE outbox SET sent_at=? WHERE id IN ({','.join('?' * len(routine))})", (now_ts, *routine))
                pending = [p for p in pending if p["step"] in ("urgent", "overdue")]
                if not pending:
                    continue
            # One line per chat, keeping the most severe step; skip chats already answered.
            best: dict[str, str] = {}
            for p in pending:
                if STEP_RANK[p["step"]] > STEP_RANK.get(best.get(p["wa_id"], ""), 0):
                    best[p["wa_id"]] = p["step"]
            items = []
            for wa_id, step in best.items():
                conv = self.store.one("SELECT state FROM conversations WHERE wa_id=?", (wa_id,))
                if conv and conv["state"] == AWAITING_US:
                    items.append((wa_id, step))
            items.sort(key=lambda it: -STEP_RANK[it[1]])
            # Items over the per-message limit stay queued for the next batch.
            waiting = {w for w, _ in items}
            items = items[: self.cfg.max_items_per_message]
            done = {w for w, _ in items} | ({p["wa_id"] for p in pending} - waiting)
            if items:
                text, buttons = self.render(items, now)
                mid = self.channel.send(cid, text, buttons)
                self.store.x("INSERT INTO sent_batches(chat_id,sent_at,text,shadow,telegram_message_id) VALUES(?,?,?,?,?)",
                             (cid, now_ts, text, 0 if self.cfg.live else 1, mid))
                self.store.audit("system", "alert_sent", None, {"chat_id": cid, "items": [w for w, _ in items]}, now_ts)
            ids = [p["id"] for p in pending if p["wa_id"] in done]
            if ids:
                self.store.x(f"UPDATE outbox SET sent_at=? WHERE id IN ({','.join('?' * len(ids))})", (now_ts, *ids))

    # -- rendering --------------------------------------------------------
    def item_lines(self, wa_id: str, now: dt.datetime, step: str | None = None) -> str:
        c = self.store.one("SELECT * FROM conversations WHERE wa_id=?", (wa_id,))
        ct = self.store.one("SELECT * FROM contacts WHERE wa_id=?", (wa_id,))
        last = self.store.one(
            "SELECT id, type, text FROM messages WHERE wa_id=? AND direction='in' ORDER BY ts DESC LIMIT 1", (wa_id,)
        )
        name = html.escape((ct["name"] if ct and ct["name"] else "Tanpa nama"))
        kind = KIND_LABEL.get(ct["kind"] if ct else "new", "Kontak")
        prio = f" · {c['priority']}" if c and c["priority"] else ""
        waited = ""
        if c and c["waiting_since"]:
            since = dt.datetime.fromtimestamp(c["waiting_since"], dt.timezone.utc)
            bmin = self.clock.business_minutes_between(since, now)
            real = (now - since).total_seconds() / 60
            waited = f"⏱ {human_duration(real)}" + (f" ({human_duration(bmin)} dalam jam kerja)" if abs(real - bmin) > 5 else "")
        snippet = ""
        if last:
            body = last["text"] or f"[{last['type']}]"
            body = defang(redact(body.replace("\n", " ")))
            snippet = "💬 “" + html.escape(body[:90] + ("…" if len(body) > 90 else "")) + "”"
        who = self.cfg.admin(c["assignee"]).name if c and c["assignee"] and self.cfg.admin(c["assignee"]) else "-"
        head = f"{STEP_ICON.get(step, '🟠')} <b>{name}</b> ({display(wa_id)}) · {kind}{prio}"
        if step:
            head += f"\n{STEP_LABEL[step]}"
        ai_line = f"🤖 {html.escape(defang(c['ai_summary']))}" if c and c["ai_summary"] and not c["ai_pending"] else ""
        draft_line = ""
        if c and c["draft_text"] and self._draft_current(c):
            reply, missing, can = parse_draft(c)
            if can and reply:
                draft_line = f"💡 <b>Saran balasan</b> (salin ke WhatsApp jika cocok):\n<i>{html.escape(defang(reply))}</i>"
            elif missing:
                draft_line = f"💡 AI belum bisa menjawab: {html.escape(missing)}" + (
                    f"\n<i>{html.escape(reply)}</i>" if reply else "")
        link_line = html.escape(link_alert(self.store, last["id"])) if last else ""
        parts = [head, f"{waited} · PIC: {html.escape(who)}".strip(" ·"), ai_line, snippet, link_line, draft_line,
                 f'<a href="https://wa.me/{wa_id}">Buka chat</a>']
        return "\n".join(p for p in parts if p)

    def _draft_current(self, c) -> bool:
        last = self.store.one("SELECT id FROM messages WHERE wa_id=? AND direction='in' ORDER BY ts DESC LIMIT 1", (c["wa_id"],))
        return bool(last and last["id"] == c["draft_msg_id"])

    def render(self, items: list[tuple[str, str]], now: dt.datetime) -> tuple[str, list[list[Button]]]:
        if len(items) == 1:
            wa_id, step = items[0]
            text = self.item_lines(wa_id, now, step)
            buttons = [
                [Button("✋ Saya ambil", f"claim:{wa_id}"), Button("✅ Sudah dibalas", f"done:{wa_id}")],
                [Button("⏰ Tunda 1 jam", f"snooze:{wa_id}")],
            ]
            return text, buttons
        blocks = [f"{i}. " + self.item_lines(wa_id, now, step) for i, (wa_id, step) in enumerate(items, 1)]
        legend = "Tombol: ✋ Saya ambil · ✅ Sudah dibalas · ⏰ Tunda 1 jam"
        text = f"<b>{len(items)} chat perlu dibalas</b>\n\n" + "\n\n".join(blocks) + f"\n\n<i>{legend}</i>"
        buttons = [
            [Button(f"✋{i}", f"claim:{w}"), Button(f"✅{i}", f"done:{w}"), Button(f"⏰{i}", f"snooze:{w}")]
            for i, (w, _) in enumerate(items, 1)
        ]
        return text, buttons

    # -- queues & morning list ------------------------------------------
    def queue_text(self, admin_key: str | None, now: dt.datetime, limit: int = 15) -> str:
        """`/antrian` (admin_key set) or `/semua` (None): waiting chats, oldest first."""
        sql = "SELECT wa_id FROM conversations WHERE state=?"
        params: list = [AWAITING_US]
        if admin_key:
            sql += " AND assignee=?"
            params.append(admin_key)
        rows = self.store.q(sql + " ORDER BY waiting_since ASC", tuple(params))
        possibly = self.store.q("SELECT wa_id FROM conversations WHERE state=? ORDER BY state_since ASC", (POSSIBLY_REPLIED,))
        title = "Antrian kamu" if admin_key else "Semua chat yang menunggu"
        if not rows:
            text = f"🟢 <b>{title}</b>: kosong. Mantap!"
        else:
            shown = rows[:limit]
            text = f"<b>{title}</b>: {len(rows)} chat (terlama di atas)\n\n" + "\n\n".join(
                f"{i}. " + self.item_lines(r["wa_id"], now) for i, r in enumerate(shown, 1)
            )
            if len(rows) > limit:
                text += f"\n\n…dan {len(rows) - limit} lainnya."
        if possibly and not admin_key:
            text += "\n\n<b>Mungkin sudah dibalas</b> (klien berterima kasih, tapi balasan tidak terlihat):\n" + "\n".join(
                f"• {display(r['wa_id'])} <a href=\"https://wa.me/{r['wa_id']}\">Buka</a>" for r in possibly[:10]
            )
        return text

    def morning_list(self, now: dt.datetime) -> None:
        """Once per business day, within an hour of the configured time (not after a late restart)."""
        local = now.astimezone(self.cfg.tz)
        start = dt.datetime.combine(local.date(), self.cfg.admin_morning_time, self.cfg.tz)
        if not self.clock.is_business_day(local.date()) or not start <= local < start + dt.timedelta(hours=1):
            return
        key = f"morning:{local.date().isoformat()}"
        if self.store.get_kv(key):
            return
        self.store.set_kv(key, str(int(time.time())))
        waiting = self.store.q("SELECT assignee FROM conversations WHERE state IN (?,?)", (AWAITING_US, POSSIBLY_REPLIED))
        spam = self.store.one("SELECT COUNT(*) n FROM intake WHERE status='spam' AND updated_at>?",
                              (int(now.timestamp()) - 86400,))["n"]
        if not waiting and not spam:
            return
        spam_line = f"\n\n🗑️ {spam} pesan diduga spam kemarin (tidak dibalas). Cek dengan /spam." if spam else ""
        mine = {r["assignee"] for r in self.store.q("SELECT assignee FROM conversations WHERE state=?", (AWAITING_US,))}
        for a in self.cfg.admins:
            if a.telegram_id and a.key in mine:
                self.channel.send(a.telegram_id, "☀️ Selamat pagi!\n\n" + self.queue_text(a.key, now))
        for s in self.cfg.supervisors:
            if s.telegram_id:
                self.channel.send(s.telegram_id, "☀️ Ringkasan pagi\n\n" + self.queue_text(None, now) + spam_line)
        if self.cfg.admin_group_id:
            self.channel.send(self.cfg.admin_group_id, "☀️ Ringkasan pagi\n\n" + self.queue_text(None, now) + spam_line)
        self.store.audit("system", "morning_list", None, None, int(now.timestamp()))
