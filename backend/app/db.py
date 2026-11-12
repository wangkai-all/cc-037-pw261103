"""SQLite access layer.

Single-file database, one connection per request (check_same_thread=False is
not needed because we open connections on demand and never share them).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("SNAPSHOT_DB", Path(__file__).resolve().parent.parent / "data" / "snapshot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS snapshots (
    id                  INTEGER PRIMARY KEY,
    snapshot_token      TEXT NOT NULL UNIQUE,
    status              TEXT NOT NULL DEFAULT 'collecting',  -- collecting | complete | committed
    page_size           INTEGER,
    total_count         INTEGER,
    fetched_count       INTEGER NOT NULL DEFAULT 0,
    next_cursor         TEXT,
    expected_page_index INTEGER NOT NULL DEFAULT 0,
    fail_reason         TEXT,
    is_current          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    completed_at        TEXT,
    committed_at        TEXT
);

CREATE TABLE IF NOT EXISTS fetch_logs (
    id               INTEGER PRIMARY KEY,
    snapshot_id      INTEGER NOT NULL REFERENCES snapshots(id),
    seq              INTEGER NOT NULL,
    request_token    TEXT,
    page_index       INTEGER,
    returned_count   INTEGER,
    next_page_token  TEXT,
    ok               INTEGER NOT NULL,
    error            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetch_logs_snapshot ON fetch_logs(snapshot_id);

-- Provisional rows while collecting; authoritative once the snapshot is committed.
CREATE TABLE IF NOT EXISTS snapshot_records (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id),
    business_key  TEXT NOT NULL,
    version       TEXT NOT NULL,
    payload       TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, business_key)
);

-- Diff materialised at commit time only.
CREATE TABLE IF NOT EXISTS diff_records (
    id           INTEGER PRIMARY KEY,
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id),
    baseline_id  INTEGER REFERENCES snapshots(id),
    business_key TEXT NOT NULL,
    diff_type    TEXT NOT NULL,  -- added | modified | suspected_missing | confirmed_deleted | reappeared
    old_version  TEXT,
    new_version  TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (snapshot_id, business_key)
);

-- Append-only deletion history. The UNIQUE constraint is the database-level
-- guarantee that re-submitting the same batch cannot create duplicate deletes.
CREATE TABLE IF NOT EXISTS delete_events (
    id           INTEGER PRIMARY KEY,
    business_key TEXT NOT NULL,
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id),
    baseline_id  INTEGER REFERENCES snapshots(id),
    created_at   TEXT NOT NULL,
    UNIQUE (snapshot_id, business_key)
);
CREATE INDEX IF NOT EXISTS idx_delete_events_key ON delete_events(business_key);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def wipe_db() -> None:
    """Drop every table and recreate the schema.

    Deleting the SQLite file while other connections still have it open is
    unreliable (the unlinked inode keeps serving stale state), so we reset in
    place instead. Sequence matters: disable FK enforcement first, otherwise
    the child tables cannot be dropped.
    """
    conn = get_conn()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        for name in tables:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
