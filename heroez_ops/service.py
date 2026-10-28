"""Wires config, storage, engine, notifier and bot together (no web framework here)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import time

from .alerts.base import AlertChannel
from .alerts.telegram import ShadowAlertChannel, TelegramAlertChannel
from .bizhours import BusinessClock
from .bot import Bot
from .config import Config
from .db import Store
from .notifier import Notifier
from .sources.base import MessageEvent
from .sources.meta import MetaCoexistenceSource
from .states import Engine

log = logging.getLogger(__name__)


class Service:
    def __init__(self, cfg: Config, store: Store, alert_channel: AlertChannel | None = None,
                 reply_channel: AlertChannel | None = None):
        self.cfg, self.store = cfg, store
        self.clock = BusinessClock(cfg)
        self.source = MetaCoexistenceSource(cfg.meta_app_secret, cfg.business_number)
        self.engine = Engine(cfg, store)
        telegram = TelegramAlertChannel(cfg.telegram_bot_token) if cfg.telegram_bot_token else None
        # Shadow mode: alerts are only logged. Command replies still go to Telegram so
        # management can use /semua during the shadow week.
        self.alert_channel = alert_channel or (telegram if cfg.live and telegram else ShadowAlertChannel())
        self.reply_channel = reply_channel or telegram or self.alert_channel
        self.notifier = Notifier(cfg, store, self.clock, self.alert_channel)
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

    # -- periodic -----------------------------------------------------------
    def tick(self, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now(dt.timezone.utc)
        self.process_inbox()
        self.notifier.tick(now)
        self.check_quiet_inbox(now)

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
