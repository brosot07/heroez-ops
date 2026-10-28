"""SQLite storage. All timestamps are UTC epoch seconds (int).

SQLite in WAL mode is enough for one small server and one process; the file lives
on an encrypted volume (see OPERATIONS.md). Every write goes through Store.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    wa_id TEXT PRIMARY KEY,
    name TEXT,
    kind TEXT NOT NULL DEFAULT 'new',        -- talent | existing_client | new
    handler TEXT,                            -- admin key from roster / sticky owner
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,                     -- Meta message id (wamid), used for dedupe
    wa_id TEXT NOT NULL,
    direction TEXT NOT NULL,                 -- in | out
    source TEXT NOT NULL,                    -- messages | echo | history
    ts INTEGER NOT NULL,
    type TEXT NOT NULL,
    text TEXT,
    raw TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_wa_ts ON messages(wa_id, ts);
CREATE TABLE IF NOT EXISTS conversations (
    wa_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    state_reason TEXT,
    state_since INTEGER NOT NULL,
    waiting_since INTEGER,                   -- first unanswered inbound while AWAITING_US
    last_inbound_at INTEGER,
    last_outbound_at INTEGER,
    assignee TEXT,
    assigned_how TEXT,                       -- roster | sticky | round_robin | claim
    snoozed_until INTEGER,
    priority TEXT,
    fired_steps TEXT NOT NULL DEFAULT '[]',  -- escalation steps already fired for this wait
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    message_id TEXT,
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,                -- Telegram chat (user DM or group)
    wa_id TEXT NOT NULL,
    step TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    sent_at INTEGER,
    batch_message_id TEXT
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(chat_id, sent_at);
CREATE TABLE IF NOT EXISTS sent_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    sent_at INTEGER NOT NULL,
    text TEXT NOT NULL,
    shadow INTEGER NOT NULL,
    telegram_message_id INTEGER
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id TEXT NOT NULL,
    kind TEXT NOT NULL,                      -- not_ours | already_replied
    admin TEXT,
    state_before TEXT,
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    actor TEXT NOT NULL,                     -- system | admin key | telegram id
    action TEXT NOT NULL,
    wa_id TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS waits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,    -- one row per finished AWAITING_US period
    wa_id TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at INTEGER NOT NULL,
    assignee TEXT,
    ended_by TEXT NOT NULL                   -- state it moved to (AWAITING_THEM = replied)
);
CREATE INDEX IF NOT EXISTS waits_ended ON waits(ended_at);
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,    -- raw webhook payloads, stored before acking
    source TEXT NOT NULL,
    body TEXT NOT NULL,
    received_at INTEGER NOT NULL,
    processed_at INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS inbox_pending ON inbox(processed_at);
CREATE TABLE IF NOT EXISTS ai_usage (
    day TEXT NOT NULL,
    model TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, model)
);
CREATE TABLE IF NOT EXISTS contract_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL,                    -- analyzing | review | approved | archived | failed
    spec TEXT,                               -- fields JSON (see doctemplate.py)
    notes TEXT,                              -- AI notes / problems, JSON list
    check_ok INTEGER NOT NULL DEFAULT 0,
    check_msg TEXT,
    created_at INTEGER NOT NULL,
    created_by TEXT,
    approved_at INTEGER,
    approved_by TEXT
);
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    number TEXT NOT NULL,
    client_name TEXT,
    total INTEGER,
    data TEXT NOT NULL,                      -- {"values": {...}, "rows": [...]}
    created_at INTEGER NOT NULL,
    created_by TEXT,
    updated_at INTEGER NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS kb (
    name TEXT PRIMARY KEY,                   -- faq | info | texts | ratecard_media
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS api_sends (
    month TEXT NOT NULL,
    purpose TEXT NOT NULL,                   -- intake | faq | draft
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (month, purpose)
);
CREATE TABLE IF NOT EXISTS intake (
    wa_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,                    -- active | done | stopped
    step TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'id',
    data TEXT NOT NULL DEFAULT '{}',
    first_msg_ts INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    outcome TEXT
);
CREATE TABLE IF NOT EXISTS talents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    data TEXT NOT NULL,                      -- {"info": {...public...}, "rates": {"Rate IG Reels": 15000000}}
    phone TEXT,                              -- private: routing only
    handler TEXT,                            -- private: admin key
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS talent_busy (
    talent_id INTEGER NOT NULL,              -- only dates are stored, never what the booking is
    start TEXT NOT NULL,
    end TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS link_checks (
    url_hash TEXT PRIMARY KEY,               -- URLhaus answers, cached a day (the link itself is not stored)
    verdict TEXT NOT NULL,
    checked_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS message_links (
    msg_id TEXT NOT NULL,
    wa_id TEXT NOT NULL,
    shown TEXT NOT NULL,                     -- defanged form only
    host TEXT,
    level TEXT NOT NULL,                     -- ok | unknown | suspicious | malicious
    reasons TEXT,
    PRIMARY KEY (msg_id, shown)
);
CREATE TABLE IF NOT EXISTS doc_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    status TEXT NOT NULL,                    -- reading | review | approved | failed | cancelled
    result TEXT,                             -- {"talents": [...], "document_notes": [...]}
    error TEXT,
    created_at INTEGER NOT NULL,
    created_by TEXT,
    decided_at INTEGER,
    decided_by TEXT
);
CREATE TABLE IF NOT EXISTS reply_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id TEXT,
    client_text TEXT NOT NULL,               -- redacted
    admin_reply TEXT NOT NULL,               -- redacted
    status TEXT NOT NULL,                    -- candidate | approved
    created_at INTEGER NOT NULL,
    approved_at INTEGER,
    approved_by TEXT
);
CREATE TABLE IF NOT EXISTS draft_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    draft TEXT NOT NULL,
    actual TEXT NOT NULL,
    similarity REAL NOT NULL,
    outcome TEXT NOT NULL,                   -- used | edited | ignored
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    contact_name TEXT NOT NULL DEFAULT '',
    pic TEXT,                                -- admin key
    talents TEXT NOT NULL DEFAULT '',
    period_start TEXT,
    period_end TEXT,
    value INTEGER NOT NULL DEFAULT 0,        -- Rp, from the contract when one exists
    stage TEXT NOT NULL,
    stages TEXT NOT NULL DEFAULT '{}',       -- {stage: unix ts when reached}
    source TEXT NOT NULL DEFAULT '',         -- bot | chat | contract | manual:<who>
    next_action TEXT,
    last_activity INTEGER,
    contract_id INTEGER,
    contract_number TEXT,
    notes TEXT,
    deal_asked_at INTEGER,                   -- last time an admin was asked "is this a deal?"
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS deals_wa ON deals(wa_id, stage);
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id TEXT NOT NULL,
    admin TEXT,                              -- admin key who promised (assignee at the time)
    text TEXT NOT NULL,                      -- redacted sentence
    due_at INTEGER NOT NULL,
    status TEXT NOT NULL,                    -- open | done | expired
    created_at INTEGER NOT NULL,
    reminded_at INTEGER,
    escalated_at INTEGER,
    done_at INTEGER,
    done_by TEXT
);
CREATE INDEX IF NOT EXISTS promises_open ON promises(status, due_at);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: str | Path = "data/heroez.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA secure_delete=ON")      # erased text is overwritten, not left in free pages
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()
        self._migrate()

    # Columns added after the first release. Old databases get them on start-up.
    ADDED_COLUMNS = {
        "conversations": [
            ("ai_pending", "INTEGER NOT NULL DEFAULT 0"),   # last inbound message not yet checked by AI
            ("ai_msg_id", "TEXT"),                          # message the AI verdict refers to
            ("ai_kind", "TEXT"),
            ("ai_summary", "TEXT"),
            ("ai_confidence", "REAL"),
            ("draft_text", "TEXT"),                         # AI reply suggestion for the current wait
            ("draft_msg_id", "TEXT"),                       # client message the draft answers
            ("draft_can_answer", "INTEGER"),
            ("triage_since", "INTEGER"),                   # waiting for a supervisor to pick the admin
        ],
        "talents": [("calendar_url", "TEXT")],                # private: the KOL's own iCal address
        "talent_busy": [("source", "TEXT NOT NULL DEFAULT 'upload'")],   # upload | calendar
    }

    def _migrate(self) -> None:
        for table, cols in self.ADDED_COLUMNS.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # -- generic helpers -------------------------------------------------
    def q(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def x(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def tx(self):
        """Context manager for a transaction: `with store.tx(): ...`."""
        return _Tx(self)

    def backup(self, dest: str | Path) -> None:
        """Consistent online copy of the database (safe while the service runs)."""
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(dest))
        try:
            with self.lock:
                self.conn.backup(target)
        finally:
            target.close()

    def vacuum(self) -> None:
        with self.lock:
            self.conn.execute("VACUUM")

    def forget_number(self, wa_id: str) -> dict:
        """Erase everything about one WhatsApp number (data-subject request)."""
        counts = {}
        with self.tx():
            for table in ("messages", "conversations", "state_changes", "waits", "feedback", "intake", "message_links",
                          "outbox", "contacts"):
                counts[table] = self.x(f"DELETE FROM {table} WHERE wa_id=?", (wa_id,)).rowcount
            counts["audit_log"] = self.x("UPDATE audit_log SET wa_id='(dihapus)', detail=NULL WHERE wa_id=?", (wa_id,)).rowcount
        return counts

    # -- kv / audit ------------------------------------------------------
    def get_kv(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    def set_kv(self, key: str, value: str) -> None:
        self.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def audit(self, actor: str, action: str, wa_id: str | None = None, detail: dict | str | None = None, at: int | None = None) -> None:
        if isinstance(detail, dict):
            detail = json.dumps(detail, ensure_ascii=False)
        self.x(
            "INSERT INTO audit_log(at,actor,action,wa_id,detail) VALUES(?,?,?,?,?)",
            (at or int(time.time()), actor, action, wa_id, detail),
        )


class _Tx:
    def __init__(self, store: Store):
        self.store = store

    def __enter__(self):
        self.store.lock.acquire()
        self.store.conn.execute("BEGIN IMMEDIATE")
        return self.store

    def __exit__(self, exc_type, exc, tb):
        try:
            self.store.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            self.store.lock.release()
        return False
