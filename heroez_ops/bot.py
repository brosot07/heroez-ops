"""Handles Telegram updates: button presses and admin commands. Whitelisted users only."""
from __future__ import annotations

import datetime as dt
import html
import logging

from .alerts.base import AlertChannel, Button
from .config import Config
from .db import Store
from .notifier import Notifier
from .phone import display, normalize
from .states import Engine

log = logging.getLogger(__name__)

HELP = (
    "<b>Asisten Heroez</b>\n"
    "/antrian – chat yang menunggu balasan kamu\n"
    "/semua – semua chat yang menunggu\n"
    "/cari &lt;nama atau nomor&gt; – cari kontak\n"
    "/bantuan – pesan ini\n\n"
    "Tombol di notifikasi:\n"
    "✋ Saya ambil – chat jadi tanggung jawab kamu\n"
    "✅ Sudah dibalas – kalau kamu sudah balas tapi sistem belum tahu\n"
    "⏰ Tunda 1 jam – ingatkan lagi nanti\n"
    "🚫 Bukan urusan kita – chat ini tidak perlu dibalas"
)

TOAST = {
    "claim": "✋ Oke, chat ini jadi tanggung jawab kamu.",
    "done": "✅ Dicatat sudah dibalas.",
    "snooze": "⏰ Ditunda 1 jam.",
    "nope": "🚫 Dicatat: tidak perlu dibalas.",
}


class Bot:
    def __init__(self, cfg: Config, store: Store, engine: Engine, notifier: Notifier, channel: AlertChannel):
        self.cfg, self.store, self.engine, self.notifier, self.channel = cfg, store, engine, notifier, channel

    def _actor(self, user_id: int) -> str | None:
        """Admin key, 'mgmt:<id>' for management, or None if not allowed."""
        a = self.cfg.admin_by_telegram(user_id)
        if a:
            return a.key
        if user_id in self.cfg.management_user_ids:
            return f"mgmt:{user_id}"
        return None

    def handle_update(self, update: dict, now: dt.datetime) -> None:
        if "callback_query" in update:
            self._callback(update["callback_query"], now)
        elif "message" in update:
            self._message(update["message"], now)

    def _callback(self, cq: dict, now: dt.datetime) -> None:
        user_id = (cq.get("from") or {}).get("id", 0)
        actor = self._actor(user_id)
        if not actor:
            self.channel.answer_callback(cq.get("id", ""), "Maaf, kamu tidak punya akses.")
            self.store.audit(str(user_id), "denied_callback", None, cq.get("data"))
            return
        action, _, wa_id = (cq.get("data") or "").partition(":")
        at = int(now.timestamp())
        if not self.engine.conversation(wa_id):
            self.channel.answer_callback(cq.get("id", ""), "Chat tidak ditemukan.")
            return
        if action == "claim":
            if actor.startswith("mgmt:"):
                self.channel.answer_callback(cq["id"], "Hanya admin yang bisa mengambil chat.")
                return
            self.engine.claim(wa_id, actor, at)
        elif action == "done":
            self.engine.mark_replied(wa_id, actor, at)
        elif action == "snooze":
            self.engine.snooze(wa_id, actor, at)
        elif action == "nope":
            self.engine.mark_not_ours(wa_id, actor, at)
        else:
            return
        self.channel.answer_callback(cq["id"], TOAST[action])

        self._update_alert(cq.get("message") or {}, action, wa_id, actor, now)

    def _update_alert(self, msg: dict, action: str, wa_id: str, actor: str, now: dt.datetime) -> None:
        """Leave a visible trace on the alert so everyone sees who handled it."""
        if not msg.get("message_id"):
            return
        markup = (msg.get("reply_markup") or {}).get("inline_keyboard") or []
        rows = [[Button(b["text"], b["callback_data"]) for b in row] for row in markup]
        chat_id = msg["chat"]["id"]
        resolved = action in ("done", "nope")
        single = rows and all(b.data.endswith(":" + wa_id) for row in rows for b in row)
        if single:
            who = self.cfg.admin(actor).name if self.cfg.admin(actor) else "Manajemen"
            note = {"claim": f"✋ Diambil {who}", "done": f"🟢 Sudah dibalas ({who})",
                    "snooze": f"⏰ Ditunda 1 jam ({who})", "nope": f"🟢 Tidak perlu dibalas ({who})"}[action]
            text = self.notifier.item_lines(wa_id, now) + "\n\n" + html.escape(note)
            self.channel.edit(chat_id, msg["message_id"], text, None if resolved else rows)
        elif resolved:
            # Batch alert: drop the handled chat's button row, keep the rest.
            rows = [row for row in rows if not any(b.data.endswith(":" + wa_id) for b in row)]
            self.channel.edit_buttons(chat_id, msg["message_id"], rows)

    def _message(self, msg: dict, now: dt.datetime) -> None:
        user_id = (msg.get("from") or {}).get("id", 0)
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        actor = self._actor(user_id)
        if cmd == "/start" and not actor:
            # Lets a new admin find their Telegram ID to give to whoever edits config.
            self.channel.send(chat_id, f"Halo! ID Telegram kamu: <code>{user_id}</code>\nKirim ID ini ke pengelola sistem supaya didaftarkan.")
            return
        if not actor:
            self.store.audit(str(user_id), "denied_command", None, cmd)
            return
        if cmd in ("/start", "/bantuan", "/help"):
            self.channel.send(chat_id, HELP)
        elif cmd == "/antrian":
            key = actor if not actor.startswith("mgmt:") else None
            self.channel.send(chat_id, self.notifier.queue_text(key, now))
        elif cmd == "/semua":
            self.channel.send(chat_id, self.notifier.queue_text(None, now))
        elif cmd == "/cari":
            self.channel.send(chat_id, self.search(arg.strip(), now))
        self.store.audit(actor, "command", None, cmd)

    def search(self, q: str, now: dt.datetime) -> str:
        if not q:
            return "Tulis nama atau nomor, contoh: /cari 0812345 atau /cari Rina"
        num = normalize(q, local=True)
        rows = self.store.q(
            "SELECT wa_id FROM contacts WHERE name LIKE ? OR (? != '' AND wa_id LIKE ?) LIMIT 5",
            (f"%{q}%", num, f"%{num}%"),
        )
        if not rows:
            return f"Tidak ada kontak yang cocok dengan “{html.escape(q)}”."
        out = []
        for r in rows:
            c = self.engine.conversation(r["wa_id"])
            state = c["state"] if c else "-"
            out.append(self.notifier.item_lines(r["wa_id"], now) + f"\nStatus: {state}")
        return "\n\n".join(out)
