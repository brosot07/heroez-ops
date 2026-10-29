"""Bot reply approval: in "cek dulu" mode every message the bot wants to send to a client is shown
to the supervisors on Telegram first. Nothing reaches the client until someone taps ✅ Kirim.

In Shadow/Review mode the same cards are a preview only (👍/👎), so the team can read what the bot
would say before it ever talks to a client.
"""
from __future__ import annotations

import html
import json
import logging

from .alerts.base import AlertChannel, Button
from .config import Config
from .db import Store
from .links import defang
from .phone import display
from .textrules import redact

log = logging.getLogger(__name__)

REPLY_MODES = ("off", "approve", "auto")
MODE_LABELS = {"off": "Mati", "approve": "Cek dulu (disetujui supervisor)", "auto": "Otomatis"}
CARD_BODY_MAX = 1500


def effective_mode(cfg: Config, store: Store) -> str:
    """Telegram override (/bot mati, /bot cek) > settings page > legacy intake_enabled flag."""
    m = store.get_kv("bot_reply_mode") or cfg.bot_reply_mode or ("auto" if cfg.intake_enabled else "off")
    return m if m in REPLY_MODES else "off"


class ReplyApproval:
    def __init__(self, cfg: Config, store: Store, channel: AlertChannel):
        self.cfg, self.store, self.channel = cfg, store, channel

    def recipients(self) -> list[int]:
        return list(dict.fromkeys(s.telegram_id for s in self.cfg.supervisors if s.telegram_id))

    # ---------------------------------------------------------------- cards
    def show(self, pid: int, live: bool) -> None:
        p = self.store.one("SELECT * FROM bot_outbox WHERE id=?", (pid,))
        if not p:
            return
        c = self.store.one("SELECT name FROM contacts WHERE wa_id=?", (p["wa_id"],))
        last = self.store.one("SELECT text FROM messages WHERE wa_id=? AND direction='in' ORDER BY ts DESC LIMIT 1", (p["wa_id"],))
        name = html.escape(defang((c["name"] if c else "") or "") or display(p["wa_id"]))
        client = html.escape(defang(redact(((last["text"] if last else "") or "")[:300])))
        args = json.loads(p["args"])
        extra = ""
        if p["kind"] == "buttons":
            extra = "\nTombol: " + " · ".join(f"[{html.escape(o[1])}]" for o in args[1])
        elif p["kind"] == "list":
            extra = "\nPilihan: " + " · ".join(html.escape(o[1]) for o in args[2])
        elif p["kind"] == "ratecard":
            extra = "\n📎 Rate card Heroez.pdf"
        body = html.escape(p["body"][:CARD_BODY_MAX]) + ("…" if len(p["body"]) > CARD_BODY_MAX else "")
        head = ("🤖 <b>Balasan bot menunggu persetujuan</b>" if live
                else "🧪 <b>Contoh balasan bot</b> (mode uji: tidak dikirim ke klien)")
        text = (f"{head}\n👤 <b>{name}</b> ({display(p['wa_id'])})\n💬 Klien: “{client}”\n\n"
                f"✉️ Bot akan membalas:\n{body}{extra}")
        if live:
            text += f"\n\n<i>Tidak disetujui dalam {self.cfg.bot_approve_timeout_minutes} menit → diserahkan ke admin.</i>"
            buttons = [[Button("✅ Kirim", f"bok:{p['wa_id']}:{pid}"), Button("❌ Jangan, serahkan ke admin", f"bno:{p['wa_id']}:{pid}")]]
        else:
            buttons = [[Button("👍 Bagus", f"bok:{p['wa_id']}:{pid}"), Button("👎 Kurang tepat", f"bno:{p['wa_id']}:{pid}")]]
        cards = []
        for chat in self.recipients():
            mid = self.channel.send(chat, text, buttons)
            if mid:
                cards.append([chat, mid])
        if not self.recipients():
            log.warning("bot reply %s waits for approval but no supervisor has a Telegram ID", pid)
        self.store.x("UPDATE bot_outbox SET card_text=?, cards=? WHERE id=?", (text, json.dumps(cards), pid))

    def close(self, pids: list[int], note: str) -> None:
        """Mark the cards of these messages as handled (for every supervisor who got them)."""
        for pid in pids:
            p = self.store.one("SELECT card_text, cards FROM bot_outbox WHERE id=?", (pid,))
            if not p or not p["cards"]:
                continue
            for chat, mid in json.loads(p["cards"]):
                try:
                    self.channel.edit(chat, mid, (p["card_text"] or "") + "\n\n" + html.escape(note), None)
                except Exception:
                    log.exception("could not update approval card %s", pid)

    # ---------------------------------------------------------------- reporting
    def stats(self, since: int, until: int) -> dict:
        rows = self.store.q("SELECT status, COUNT(*) n FROM bot_outbox WHERE created_at>=? AND created_at<? GROUP BY status",
                            (since, until))
        return {r["status"]: r["n"] for r in rows}
