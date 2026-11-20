"""
database.py
===========
SQLite persistence layer for VeriGrade.

The whole application state lives in ONE table, ``ledger``. It is append-only:
the application never updates or deletes rows itself. That is exactly what
makes the SHA-256 chain meaningful — if somebody edits a row directly with
the ``sqlite3`` shell, the audit endpoint hashes the *current* values, the
digest no longer matches, and the exact tampered row is reported.

This module knows nothing about hashing; it only moves dictionaries in and out
of SQLite. ``ledger.py`` turns those dictionaries into hashes.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

try:
    from .ledger import GENESIS_HASH
except ImportError:
    from ledger import GENESIS_HASH

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

#: Where the database lives. Override with the VERIGRADE_DB env var.
DB_PATH = os.environ.get("VERIGRADE_DB", os.path.join(BASE_DIR, "results.db"))

#: Column order used for every INSERT (kept in sync with SCHEMA_SQL).
COLUMNS = (
    "entry_type", "roll_no", "student_name", "subject", "marks", "exam_session",
    "target_id", "supersedes_id", "reason", "approver_1", "approver_2",
    "prev_hash", "record_hash", "created_at",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type    TEXT NOT NULL,      -- PUBLISH | CORRECTION
    roll_no       TEXT,
    student_name  TEXT,
    subject       TEXT,
    marks         INTEGER,
    exam_session  TEXT,
    target_id     INTEGER,            -- corrections: id of the original PUBLISH row
    supersedes_id INTEGER,            -- corrections: id of the entry that was current
    reason        TEXT,
    approver_1    TEXT,
    approver_2    TEXT,
    prev_hash     TEXT,               -- hash of the previous ledger entry
    record_hash   TEXT,               -- SHA-256 of this entry (incl. prev_hash)
    created_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_key
    ON ledger (roll_no, subject, exam_session);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_record_hash
    ON ledger (record_hash);
"""


@contextmanager
def get_connection():
    """
    Yield a SQLite connection and guarantee commit/rollback + close.

    ``row_factory = sqlite3.Row`` lets us read columns by name, which keeps
    the rest of the code readable.
    """
    directory = os.path.dirname(DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the table and indexes if they do not exist yet (idempotent)."""
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)


def fetch_all_entries() -> List[Dict[str, Any]]:
    """Return every ledger row in chain order (oldest first)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM ledger ORDER BY id ASC").fetchall()
    return [dict(row) for row in rows]


def fetch_entry(entry_id: int) -> Optional[Dict[str, Any]]:
    """Return a single ledger row, or None."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM ledger WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def fetch_tip_hash() -> str:
    """
    Return the hash of the newest entry — the value the next entry must commit
    to. Falls back to the genesis hash for an empty ledger.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT record_hash FROM ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row and isinstance(row["record_hash"], str) and row["record_hash"]:
        return row["record_hash"]
    return GENESIS_HASH


def insert_entry(entry: Dict[str, Any]) -> int:
    """Append one row to the ledger and return its new id."""
    placeholders = ", ".join("?" for _ in COLUMNS)
    sql = f"INSERT INTO ledger ({', '.join(COLUMNS)}) VALUES ({placeholders})"
    values = [entry.get(column) for column in COLUMNS]
    with get_connection() as conn:
        cursor = conn.execute(sql, values)
        new_id = cursor.lastrowid
    return int(new_id)


def find_publish(roll_no: str, subject: str, exam_session: str) -> Optional[Dict[str, Any]]:
    """
    Locate an existing PUBLISH row for ``(roll_no, subject, exam_session)``.

    Comparison is case-insensitive and whitespace-trimmed so ``cs-01`` and
    ``CS-01`` are recognised as the same student.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM ledger "
            "WHERE entry_type = 'PUBLISH' "
            "  AND lower(trim(roll_no))     = lower(trim(?)) "
            "  AND lower(trim(subject))     = lower(trim(?)) "
            "  AND lower(trim(exam_session)) = lower(trim(?)) "
            "ORDER BY id ASC LIMIT 1",
            (roll_no, subject, exam_session),
        ).fetchone()
    return dict(row) if row else None


def publish_exists(roll_no: str, subject: str, exam_session: str) -> bool:
    """True when a publish entry already exists for this key (duplicate guard)."""
    return find_publish(roll_no, subject, exam_session) is not None