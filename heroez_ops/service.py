"""Wires config, storage, engine, notifier and bot together (no web framework here)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

from .ai import Classifier, ClaudeClassifier, build_transcript
from .alerts.base import AlertChannel
from .alerts.telegram import ShadowAlertChannel, TelegramAlertChannel
from .bizhours import BusinessClock
from .bot import Bot
from .config import Config
from .db import Store
from .digest import Digest
from .drafts import Drafter
from . import calendar_sync
from .concierge_ai import ConciergeAI
from .intake import Intake
from .kb import KB
from .links import LinkChecker
from .notifier import Notifier
from .sources.base import MessageEvent
from .sources.meta import MetaCoexistenceSource
from .sources.meta_send import MetaSender, month_sends
from .states import Engine

log = logging.getLogger(__name__)


class Service:
    def __init__(self, cfg: Config, store: Store, alert_channel: AlertChannel | None = None,
                 reply_channel: AlertChannel | None = None, data_dir: Path = Path("data"),
                 classifier: Classifier | None = None, sender: MetaSender | None = None, drafter=None):
        self.cfg, self.store = cfg, store
        self.data_dir = Path(data_dir) if data_dir else None
        self.clock = BusinessClock(cfg)
        self.source = MetaCoexistenceSource(cfg.meta_app_secret, cfg.business_number)
        self.engine = Engine(cfg, store)
        self.classifier = classifier
        if self.classifier is None and cfg.ai_enabled and cfg.anthropic_api_key:
            self.classifier = ClaudeClassifier(cfg.anthropic_api_key, store, model=cfg.ai_model,
                                               daily_cap=cfg.ai_max_calls_per_day)
        self.engine.ai_enabled = self.classifier is not None
        telegram = TelegramAlertChannel(cfg.telegram_bot_token) if cfg.telegram_bot_token else None
        self.telegram = telegram
        # Shadow mode: alerts are only logged. Command replies still go to Telegram so
        # management can use /semua during the shadow week.
        self.alert_channel = alert_channel or (
            telegram if cfg.alerts_live and telegram else ShadowAlertChannel(Path(data_dir) / "shadow_alerts.jsonl"))
        self.reply_channel = reply_channel or telegram or self.alert_channel
        self.notifier = Notifier(cfg, store, self.clock, self.alert_channel)
        self.digest = Digest(cfg, store, self.clock)
        # Phase 2: knowledge base, Cloud API sender (shadow unless live), menu bot, reply drafts.
        self.kb = KB(store)
        self.links = LinkChecker(store, cfg.urlhaus_auth_key)
        shadow_dir = Path(data_dir) if data_dir else Path("data")
        self.sender = sender or MetaSender(store, cfg.meta_access_token, cfg.phone_number_id, cfg.graph_api_version,
                                           live=cfg.live, shadow_log=shadow_dir / "shadow_alerts.jsonl")
        concierge = None
        if cfg.intake_enabled and cfg.anthropic_api_key and self.classifier is not None:
            concierge = ConciergeAI(cfg.anthropic_api_key, store, cfg.tz, model=cfg.ai_model,
                                    daily_cap=cfg.ai_max_calls_per_day)
        self.intake = Intake(cfg, store, self.engine, self.kb, self.sender, shadow_dir / "kb", ai=concierge)
        self.drafter = drafter
        if self.drafter is None and cfg.drafts_enabled and cfg.anthropic_api_key:
            self.drafter = Drafter(cfg.anthropic_api_key, store, self.kb, cfg.tz, model=cfg.drafts_model,
                                   max_per_day=cfg.max_drafts_per_day)
        if self.drafter:
            self.notifier.before_alert = self.drafter.ensure
        reply_notifier = Notifier(cfg, store, self.clock, self.reply_channel)
        self.bot = Bot(cfg, store, self.engine, reply_notifier, self.reply_channel, intake=self.intake)
        self.bot.service = self

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
            if (isinstance(ev, MessageEvent) and ev.direction == "in" and ev.source != "history"
                    and not self.engine.last_was_duplicate and ev.text):
                try:
                    self.links.check_message(ev.msg_id, ev.wa_id, ev.text)   # never opens the links
                except Exception:
                    log.exception("link check failed")
            if isinstance(ev, MessageEvent) and not self.engine.last_was_duplicate:
                try:
                    self.intake.on_event(ev)
                except Exception:
                    log.exception("intake failed for %s", ev.wa_id)

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
        self.intake.expire(int(now.timestamp()))
        self.classify_pending(now)
        self.notifier.tick(now)
        self.digest.maybe_send(now, self.alert_channel)
        self.check_quiet_inbox(now)
        self.daily_maintenance(now)
        self.check_send_budget(now)
        calendar_sync.maybe_sync(self.store, self.cfg.tz, now.timestamp())

    def check_send_budget(self, now: dt.datetime) -> None:
        """Warn supervisors once a month when bot sends pass 80% of the monthly budget."""
        used = month_sends(self.store)
        month = now.astimezone(self.cfg.tz).strftime("%Y-%m")
        if used < 0.8 * self.cfg.monthly_send_budget or self.store.get_kv(f"send_budget_warned:{month}"):
            return
        self.store.set_kv(f"send_budget_warned:{month}", "1")
        text = (f"⚠️ Bot sudah mengirim {used} pesan bulan ini (batas {self.cfg.monthly_send_budget}). "
                f"Meta gratis sampai 1.000 pesan per bulan; setelah itu ±Rp400 per pesan. "
                f"Bot berhenti otomatis saat batas tercapai. Ubah batas di Pengaturan.")
        for s in self.cfg.supervisors:
            if s.telegram_id:
                self.reply_channel.send(s.telegram_id, text)

    def classify_pending(self, now: dt.datetime, limit: int = 30) -> int:
        """Run the AI check on chats whose latest client message hasn't been checked yet."""
        if not self.classifier:
            return 0
        rows = self.store.q("SELECT c.wa_id, c.ai_msg_id, ct.kind FROM conversations c JOIN contacts ct USING(wa_id) "
                            "WHERE c.ai_pending=1 AND c.wa_id NOT IN (SELECT wa_id FROM intake WHERE status='active') "
                            "ORDER BY c.last_inbound_at LIMIT ?", (limit,))
        for r in rows:
            msgs = self.store.q("SELECT direction, ts, type, text FROM messages WHERE wa_id=? ORDER BY ts DESC LIMIT ?",
                                (r["wa_id"], self.cfg.ai_context_messages))
            transcript = build_transcript(list(reversed(msgs)), self.cfg.tz)
            kind = {"talent": "talent Heroez", "existing_client": "klien lama", "new": "kontak baru"}.get(r["kind"], r["kind"])
            verdict = self.classifier.classify(transcript, kind)
            if verdict is None:
                self.engine.ai_failed(r["wa_id"])
                continue
            self.engine.apply_verdict(r["wa_id"], r["ai_msg_id"], verdict, self.cfg.ai_min_confidence_to_close,
                                      int(now.timestamp()))
        return len(rows)

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
        cutoff = now_ts - self.cfg.message_text_days * 86400
        self.store.x("DELETE FROM sent_batches WHERE sent_at < ?", (cutoff,))
        self.store.x("UPDATE intake SET data='{}' WHERE updated_at < ? AND status != 'active'", (cutoff,))
        self.store.x("UPDATE conversations SET ai_summary=NULL, draft_text=NULL, draft_msg_id=NULL "
                     "WHERE updated_at < ? AND state != 'AWAITING_US'", (cutoff,))
        self.store.x("UPDATE state_changes SET reason='(kedaluwarsa)' WHERE at < ? AND reason != '(kedaluwarsa)'", (cutoff,))
        self.store.x("DELETE FROM message_links WHERE msg_id IN (SELECT id FROM messages WHERE ts < ?)", (cutoff,))
        self.store.x("DELETE FROM link_checks WHERE checked_at < ?", (now_ts - 7 * 86400,))
        self.store.x("UPDATE audit_log SET detail=NULL WHERE at < ? AND detail IS NOT NULL", (now_ts - 365 * 86400,))
        self.store.audit("system", "retention", None, {"texts_erased": erased, "raw_deleted": pruned}, now_ts)
        if self.data_dir:
            self._prune_shadow_log(self.data_dir / "shadow_alerts.jsonl", cutoff)
            if local.day == 1:
                try:
                    self.store.vacuum()
                except Exception:
                    log.exception("vacuum failed")
        if self.data_dir:
            backups = self.data_dir / "backups"
            try:
                self.store.backup(backups / f"heroez-{local.date().isoformat()}.db")
                for old in sorted(backups.glob("heroez-*.db"))[:-self.cfg.backup_keep]:
                    old.unlink()
            except Exception:
                log.exception("daily backup failed")

    @staticmethod
    def _prune_shadow_log(path: Path, cutoff: int) -> None:
        """Keep only recent lines of the shadow log (it holds message snippets)."""
        if not path.exists():
            return
        try:
            keep = []
            with path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        if int(json.loads(line).get("at", 0)) >= cutoff:
                            keep.append(line)
                    except (ValueError, AttributeError):
                        continue
            tmp = path.with_suffix(".tmp")
            tmp.write_text("".join(keep), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            log.exception("could not prune shadow log")

    def forget_number(self, wa_id: str, who: str) -> dict:
        counts = self.store.forget_number(wa_id)
        self.store.audit(who, "forget_number", None, {"tables": {k: v for k, v in counts.items() if v}})
        return counts

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
