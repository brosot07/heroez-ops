"""Daily management digest (Bahasa Indonesia) for supervisors and the management group."""
from __future__ import annotations

import datetime as dt
import html
import statistics

from .ai import month_cost
from .bizhours import BusinessClock
from .config import Config
from .db import Store
from .notifier import human_duration
from .phone import display
from .states import AWAITING_THEM, AWAITING_US, POSSIBLY_REPLIED

QUIET_DAYS = 5


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


class Digest:
    def __init__(self, cfg: Config, store: Store, clock: BusinessClock):
        self.cfg, self.store, self.clock = cfg, store, clock
        self.work_clock = BusinessClock.work(cfg)

    def build(self, start: dt.datetime, end: dt.datetime) -> str:
        s, e = int(start.timestamp()), int(end.timestamp())
        q1 = lambda sql, *p: self.store.one(sql, p)[0]  # noqa: E731

        inbound = q1("SELECT COUNT(*) FROM messages WHERE direction='in' AND source!='history' AND ts>=? AND ts<?", s, e)
        chats = q1("SELECT COUNT(DISTINCT wa_id) FROM messages WHERE direction='in' AND source!='history' AND ts>=? AND ts<?", s, e)
        new_contacts = q1("SELECT COUNT(*) FROM contacts WHERE kind='new' AND created_at>=? AND created_at<?", s, e)

        def crossed(step: str) -> int:
            return q1("SELECT COUNT(DISTINCT wa_id) FROM audit_log WHERE action=? AND at>=? AND at<?", f"escalate:{step}", s, e)

        # Response time per admin (business minutes), only waits that ended with a reply.
        per_admin: dict[str, list[float]] = {}
        for w in self.store.q("SELECT * FROM waits WHERE ended_by=? AND ended_at>=? AND ended_at<?", (AWAITING_THEM, s, e)):
            mins = self.work_clock.business_minutes_between(dt.datetime.fromtimestamp(w["started_at"], dt.timezone.utc),
                                                       dt.datetime.fromtimestamp(w["ended_at"], dt.timezone.utc))
            per_admin.setdefault(w["assignee"] or "-", []).append(mins)

        waiting = self.store.q("SELECT wa_id, waiting_since FROM conversations WHERE state=? ORDER BY waiting_since", (AWAITING_US,))
        possibly = q1("SELECT COUNT(*) FROM conversations WHERE state=?", POSSIBLY_REPLIED)
        quiet = self.store.q(
            "SELECT c.wa_id, ct.name, c.state_since FROM conversations c JOIN contacts ct USING(wa_id) "
            "WHERE c.state=? AND c.state_since<? AND ct.kind!='talent' ORDER BY c.state_since LIMIT 5",
            (AWAITING_THEM, e - QUIET_DAYS * 86400))
        feedback = {r["kind"]: r["n"] for r in self.store.q(
            "SELECT kind, COUNT(*) n FROM feedback WHERE at>=? AND at<? GROUP BY kind", (s, e))}

        day = start.astimezone(self.cfg.tz).strftime("%d %b %Y")
        lines = [f"📊 <b>Ringkasan harian · {day}</b>", "",
                 f"Pesan masuk: <b>{inbound}</b> dari {chats} chat · kontak baru: <b>{new_contacts}</b>",
                 f"Lewat 2 jam kerja: <b>{crossed('group')}</b> · lewat 18 jam: <b>{crossed('urgent')}</b> · lewat 24 jam: <b>{crossed('overdue')}</b>"]
        if per_admin:
            lines += ["", "<b>Waktu balas (jam kerja)</b>"]
            for key, vals in sorted(per_admin.items(), key=lambda kv: statistics.median(kv[1])):
                a = self.cfg.admin(key)
                name = html.escape(a.name if a else key)
                lines.append(f"• {name}: median {human_duration(statistics.median(vals))}, "
                             f"90% dibalas dalam {human_duration(_pct(vals, 0.9))} ({len(vals)} chat)")
        status = "🟢" if not waiting else "🟠" if len(waiting) < 5 else "🔴"
        lines += ["", f"{status} Masih menunggu balasan sekarang: <b>{len(waiting)}</b>"
                  + (f" · mungkin sudah dibalas: {possibly}" if possibly else "")]
        if waiting:
            since = dt.datetime.fromtimestamp(waiting[0]["waiting_since"], dt.timezone.utc)
            ct = self.store.one("SELECT name FROM contacts WHERE wa_id=?", (waiting[0]["wa_id"],))
            who = html.escape(ct["name"]) if ct and ct["name"] else display(waiting[0]["wa_id"])
            lines.append(f"Terlama: {who}, {human_duration((end - since).total_seconds() / 60)} "
                         f"<a href=\"https://wa.me/{waiting[0]['wa_id']}\">Buka</a>")
        if quiet:
            lines += ["", f"<b>Diam lebih dari {QUIET_DAYS} hari</b> (kita sudah balas, klien belum):"]
            for r in quiet:
                lines.append(f"• {html.escape(r['name'] or display(r['wa_id']))} "
                             f"<a href=\"https://wa.me/{r['wa_id']}\">Buka</a>")
        calls, usd = month_cost(self.store, end.astimezone(self.cfg.tz).date())
        if calls:
            lines += ["", f"🤖 AI bulan ini: {calls} pesan dicek · ±US${usd:.2f}"]
        if feedback:
            lines += ["", f"Koreksi admin: ✅ {feedback.get('already_replied', 0)} · 🚫 {feedback.get('not_ours', 0)}"]
        return "\n".join(lines)

    def recipients(self) -> list[int]:
        ids = [s.telegram_id for s in self.cfg.supervisors if s.telegram_id]
        if self.cfg.management_group_id:
            ids.append(self.cfg.management_group_id)
        return list(dict.fromkeys(ids))

    def maybe_send(self, now: dt.datetime, channel) -> bool:
        """Once a day at the configured time; covers the 24 hours before that time."""
        local = now.astimezone(self.cfg.tz)
        at = dt.datetime.combine(local.date(), self.cfg.management_digest_time, self.cfg.tz)
        key = f"digest:{local.date().isoformat()}"
        if not at <= local < at + dt.timedelta(hours=2) or self.store.get_kv(key):
            return False
        self.store.set_kv(key, "1")
        text = self.build(at - dt.timedelta(days=1), at)
        for cid in self.recipients():
            channel.send(cid, text)
        self.store.audit("system", "digest_sent", None, {"recipients": len(self.recipients())}, int(now.timestamp()))
        return True
