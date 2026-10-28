"""Handles Telegram updates: button presses and admin commands. Whitelisted users only."""
from __future__ import annotations

import datetime as dt
import html
import json
import logging

from .alerts.base import AlertChannel, Button
from .config import Config
from .db import Store
from .links import defang
from .textrules import redact
from .notifier import Notifier
from .phone import display, normalize
from .states import Engine

log = logging.getLogger(__name__)

HELP = (
    "<b>Asisten Heroez</b>\n"
    "/antrian – chat yang menunggu balasan kamu\n"
    "/semua – semua chat yang menunggu\n"
    "/cari &lt;nama atau nomor&gt; – cari kontak\n"
    "/spam – pesan yang diduga spam (tidak dibalas bot)\n"
    "/hapus &lt;nomor&gt; – supervisor: hapus semua data satu nomor (permintaan klien)\n"
    "/bantuan – pesan ini\n\n"
    "Tombol di notifikasi:\n"
    "✋ Saya ambil – chat jadi tanggung jawab kamu\n"
    "→ Nama admin – (supervisor) tugaskan lead brand baru ke admin itu\n"
    "✅ Sudah dibalas – kalau kamu sudah balas tapi sistem belum tahu\n"
    "⏰ Tunda 1 jam – ingatkan lagi nanti"
)

TOAST = {
    "claim": "✋ Oke, chat ini jadi tanggung jawab kamu.",
    "done": "✅ Dicatat sudah dibalas.",
    "snooze": "⏰ Ditunda 1 jam.",
    "nope": "🚫 Dicatat: tidak perlu dibalas.",
}


class Bot:
    def __init__(self, cfg: Config, store: Store, engine: Engine, notifier: Notifier, channel: AlertChannel,
                 intake=None):
        self.cfg, self.store, self.engine, self.notifier, self.channel = cfg, store, engine, notifier, channel
        self.intake = intake
        self.service = None   # set by Service (for /hapus)

    def _actor(self, user_id: int) -> str | None:
        """Admin key, 'spv:<key>', 'mgmt:<id>', or None if not allowed."""
        a = self.cfg.admin_by_telegram(user_id) if user_id else None
        if a:
            return a.key
        s = self.cfg.supervisor_by_telegram(user_id)
        if s:
            return f"spv:{s.key}"
        if user_id in self.cfg.management_user_ids:
            return f"mgmt:{user_id}"
        return None

    def _name(self, actor: str) -> str:
        if self.cfg.admin(actor):
            return self.cfg.admin(actor).name
        if actor.startswith("spv:") and self.cfg.supervisor(actor[4:]):
            return self.cfg.supervisor(actor[4:]).name
        return "Manajemen"

    def _remember_newcomer(self, user: dict) -> None:
        """Keep the last few unknown /start users so the settings page can offer them."""
        recent = [u for u in json.loads(self.store.get_kv("tg_newcomers", "[]")) if u["id"] != user.get("id")]
        name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or user.get("username") or ""
        recent.insert(0, {"id": user.get("id"), "name": name, "username": user.get("username") or ""})
        self.store.set_kv("tg_newcomers", json.dumps(recent[:10], ensure_ascii=False))

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
        action, _, rest = (cq.get("data") or "").partition(":")
        wa_id, _, extra = rest.partition(":")
        at = int(now.timestamp())
        if not self.engine.conversation(wa_id):
            self.channel.answer_callback(cq.get("id", ""), "Chat tidak ditemukan.")
            return
        if action == "notspam":
            if self.intake and self.intake.not_spam(wa_id, self._name(actor)):
                self.channel.answer_callback(cq["id"], "✅ Diteruskan ke admin sebagai chat biasa.")
            else:
                self.channel.answer_callback(cq["id"], "Sudah ditangani.")
            return
        if action in ("dealyes", "dealno"):
            svc = getattr(self, "service", None)
            if not (self.cfg.admin(actor) or actor.startswith("spv:")) or not svc or not extra.isdigit():
                self.channel.answer_callback(cq["id"], "Hanya admin atau supervisor.")
                return
            svc.deals.confirm_deal(int(extra), action == "dealyes", actor, at)
            note = f"🤝 Dicatat sebagai Deal ({self._name(actor)})" if action == "dealyes" else f"Belum deal ({self._name(actor)})"
            self.channel.answer_callback(cq["id"], "✅ Deal dicatat." if action == "dealyes" else "Oke, belum dicatat.")
            msg = cq.get("message") or {}
            if msg.get("message_id"):
                self.channel.edit(msg["chat"]["id"], msg["message_id"], (msg.get("text") or "") + "\n\n" + html.escape(note), None)
            return
        if action in ("fb_ok", "fb_no"):
            if not actor.startswith("spv:"):
                self.channel.answer_callback(cq["id"], "Penilaian hanya untuk supervisor.")
                return
            self.engine.record_alert_feedback(wa_id, action == "fb_ok", actor, at)
            self.channel.answer_callback(cq["id"], "👍 Dicatat, terima kasih." if action == "fb_ok" else "👎 Dicatat; chat ditutup.")
            return
        if action == "assign":
            if not actor.startswith("spv:") or not self.cfg.admin(extra):
                self.channel.answer_callback(cq["id"], "Hanya supervisor yang bisa menugaskan.")
                return
            self.engine.claim(wa_id, extra, at, by=actor)
            self.channel.answer_callback(cq["id"], f"→ Ditugaskan ke {self.cfg.person_name(extra)}.")
            self._update_alert(cq.get("message") or {}, "assign", wa_id, actor, now, extra=extra)
            return
        if action == "claim":
            if not (self.cfg.admin(actor) or actor.startswith("spv:")):
                self.channel.answer_callback(cq["id"], "Hanya admin atau supervisor yang bisa mengambil chat.")
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

    def _update_alert(self, msg: dict, action: str, wa_id: str, actor: str, now: dt.datetime, extra: str = "") -> None:
        """Leave a visible trace on the alert so everyone sees who handled it."""
        if not msg.get("message_id"):
            return
        markup = (msg.get("reply_markup") or {}).get("inline_keyboard") or []
        rows = [[Button(b["text"], b["callback_data"]) for b in row] for row in markup]
        chat_id = msg["chat"]["id"]
        resolved = action in ("done", "nope", "assign")
        single = rows and all(b.data.endswith(":" + wa_id) for row in rows for b in row)
        if single:
            who = self._name(actor)
            note = {"claim": f"✋ Diambil {who}", "done": f"🟢 Sudah dibalas ({who})",
                    "snooze": f"⏰ Ditunda 1 jam ({who})", "nope": f"🟢 Tidak perlu dibalas ({who})",
                    "assign": f"→ Ditugaskan ke {self.cfg.person_name(extra)} oleh {who}"}[action]
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
            # New admin/supervisor: their ID shows up on the settings page to be added.
            self._remember_newcomer(msg.get("from") or {})
            self.channel.send(chat_id, f"Halo! ID Telegram kamu: <code>{user_id}</code>\n"
                                       "Minta pengelola sistem menambahkan kamu di halaman Pengaturan Heroez Ops.")
            return
        if not actor:
            self.store.audit(str(user_id), "denied_command", None, cmd)
            return
        if cmd in ("/start", "/bantuan", "/help"):
            self.channel.send(chat_id, HELP)
        elif cmd == "/antrian":
            key = actor if self.cfg.admin(actor) else None
            self.channel.send(chat_id, self.notifier.queue_text(key, now))
        elif cmd == "/semua":
            self.channel.send(chat_id, self.notifier.queue_text(None, now))
        elif cmd == "/hapus":
            if not actor.startswith("spv:"):
                self.channel.send(chat_id, "Hanya supervisor yang bisa menghapus data nomor.")
            else:
                num = normalize(arg.strip(), local=True)
                if not num or not self.store.one("SELECT 1 FROM contacts WHERE wa_id=?", (num,)):
                    self.channel.send(chat_id, "Nomor tidak ditemukan. Contoh: /hapus 0812345678")
                elif self.service is None:
                    self.channel.send(chat_id, "Fitur ini belum aktif.")
                else:
                    counts = self.service.forget_number(num, actor)
                    self.channel.send(chat_id, f"🗑️ Semua data untuk {display(num)} dihapus "
                                               f"({counts.get('messages', 0)} pesan). Permintaan tercatat di audit.")
        elif cmd == "/spam":
            rows = self.store.q("SELECT i.wa_id, i.outcome, i.updated_at, c.name FROM intake i JOIN contacts c USING(wa_id) "
                                "WHERE i.status='spam' AND i.updated_at>? ORDER BY i.updated_at DESC LIMIT 10",
                                (int(now.timestamp()) - 7 * 86400,))
            if not rows:
                self.channel.send(chat_id, "🟢 Tidak ada pesan yang diduga spam dalam 7 hari terakhir.")
            for r in rows:
                last = self.store.one("SELECT text FROM messages WHERE wa_id=? AND direction='in' ORDER BY ts DESC LIMIT 1", (r["wa_id"],))
                body = html.escape(defang(redact(((last["text"] if last else "") or "")[:200])))
                self.channel.send(chat_id, f"🗑️ <b>{html.escape(defang(r['name'] or display(r['wa_id'])))}</b> ({display(r['wa_id'])})\n"
                                           f"AI: {html.escape(defang(r['outcome'] or ''))}\n💬 “{body}”",
                                  [[Button("Bukan spam → teruskan ke admin", f"notspam:{r['wa_id']}")]])
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
