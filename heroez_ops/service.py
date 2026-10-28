"""Wires config, storage, engine, notifier and bot together (no web framework here)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

from .alerts.base import AlertChannel
from .alerts.telegram import ShadowAlertChannel, TelegramAlertChannel
from .bizhours import BusinessClock
from .bot import Bot
from .config import Config
from .db import Store
from .digest import Digest
from .notifier import Notifier
from .sources.base import MessageEvent
from .sources.meta import MetaCoexistenceSource
from .states import Engine

log = logging.getLogger(__name__)


class Service:
    def __init__(self, cfg: Config, store: Store, alert_channel: AlertChannel | None = None,
                 reply_channel: AlertChannel | None = None, data_dir: Path = Path("data")):
        self.cfg, self.store = cfg, store
        self.data_dir = Path(data_dir) if data_dir else None
        self.clock = BusinessClock(cfg)
        self.source = MetaCoexistenceSource(cfg.meta_app_secret, cfg.business_number)
        self.engine = Engine(cfg, store)
        telegram = TelegramAlertChannel(cfg.telegram_bot_token) if cfg.telegram_bot_token else None
        self.telegram = telegram
        # Shadow mode: alerts are only logged. Command replies still go to Telegram so
        # management can use /semua during the shadow week.
        self.alert_channel = alert_channel or (
            telegram if cfg.live and telegram else ShadowAlertChannel(Path(data_dir) / "shadow_alerts.jsonl"))
        self.reply_channel = reply_channel or telegram or self.alert_channel
        self.notifier = Notifier(cfg, store, self.clock, self.alert_channel)
        self.digest = Digest(cfg, store, self.clock)
        self.bot = Bot(cfg, store, self.engine, Notifier(cfg, store, self.clock, self.reply_channel), self.reply_channel)

    # -- ingest -----------------------------------------------------------
    def accept_webhook(self, source: str, body: bytes) -> None:
        """Store the raw payload durably; processing happens in the worker."""
        self.store.x("INSERT INTO inbox(source,body,received_at) VALUES(?,?,?)", (source, body.decode("utf-8"), int(time.time())))
        self.store.set_kv("last_webhook_at", str(int(time.time())))

    def process_inbox(self, limit: int = 500) -> int:
        rows = self.store.q("SELECT id, body FROM inbox WHERE processed_at IS NULL ORDER BY id LIMIT ?", (limit,))
        for r in rows:
            err = None
            try:
                self.process_payload(json.loads(r["body"]))
            except Exception as e:  # keep going; the row is kept with its error for inspection
                log.exception("failed to process inbox row %s", r["id"])
                err = repr(e)[:500]
            self.store.x("UPDATE inbox SET processed_at=?, error=? WHERE id=?", (int(time.time()), err, r["id"]))
        return len(rows)

    def process_payload(self, payload: dict) -> None:
        events = self.source.parse(payload)
        # History arrives in chunks; replaying in time order gives correct final states.
        events.sort(key=lambda e: e.ts if isinstance(e, MessageEvent) else 0)
        for ev in events:
            self.engine.handle(ev)

    # -- Telegram long polling -------------------------------------------
    def poll_telegram_once(self, wait_seconds: int = 25) -> int:
        """Fetch and handle pending Telegram updates. Returns how many were handled."""
        if not self.telegram:
            return 0
        offset = int(self.store.get_kv("tg_offset", "0") or 0)
        updates = self.telegram.get_updates(offset, wait_seconds)
        for u in updates:
            try:
                self.bot.handle_update(u, dt.datetime.now(dt.timezone.utc))
            except Exception:
                log.exception("telegram update %s failed", u.get("update_id"))
            # Advance past it even on failure so one bad update can't block the queue.
            self.store.set_kv("tg_offset", str(u["update_id"] + 1))
        return len(updates)

    # -- periodic -----------------------------------------------------------
    def tick(self, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now(dt.timezone.utc)
        self.process_inbox()
        self.notifier.tick(now)
        self.digest.maybe_send(now, self.alert_channel)
        self.check_quiet_inbox(now)
        self.daily_maintenance(now)

    def daily_maintenance(self, now: dt.datetime) -> None:
        """Once a day after 03:00 WIB: erase old message texts, prune raw payloads, back up the DB."""
        local = now.astimezone(self.cfg.tz)
        key = f"maintenance:{local.date().isoformat()}"
        if local.hour < 3 or self.store.get_kv(key):
            return
        self.store.set_kv(key, "1")
        now_ts = int(now.timestamp())
        erased = self.store.x(
            "UPDATE messages SET text=NULL, raw=NULL WHERE ts < ? AND (text IS NOT NULL OR raw IS NOT NULL)",
            (now_ts - self.cfg.message_text_days * 86400,)).rowcount
        pruned = self.store.x("DELETE FROM inbox WHERE processed_at IS NOT NULL AND processed_at < ?",
                              (now_ts - self.cfg.raw_webhook_days * 86400,)).rowcount
        self.store.audit("system", "retention", None, {"texts_erased": erased, "raw_deleted": pruned}, now_ts)
        if self.data_dir:
            backups = self.data_dir / "backups"
            try:
                self.store.backup(backups / f"heroez-{local.date().isoformat()}.db")
                for old in sorted(backups.glob("heroez-*.db"))[:-self.cfg.backup_keep]:
                    old.unlink()
            except Exception:
                log.exception("daily backup failed")

    def check_quiet_inbox(self, now: dt.datetime, quiet_hours: int = 3) -> None:
        """Warn management once if no webhooks arrived for hours during business time."""
        last = int(self.store.get_kv("last_webhook_at", "0") or 0)
        if not last or not self.clock.is_open(now):
            return
        if now.timestamp() - last > quiet_hours * 3600 and self.store.get_kv("quiet_warned_for") != str(last):
            self.store.set_kv("quiet_warned_for", str(last))
            if self.cfg.management_group_id:
                self.reply_channel.send(
                    self.cfg.management_group_id,
                    f"⚠️ Tidak ada pesan WhatsApp masuk ke sistem selama {quiet_hours} jam di jam kerja. "
                    "Cek apakah koneksi Meta (coexistence) masih aktif. Admin tetap bisa bekerja di WhatsApp seperti biasa.",
                )
