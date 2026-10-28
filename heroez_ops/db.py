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
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

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
