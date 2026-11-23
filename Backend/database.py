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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from .ledger import GENESIS_HASH
except ImportError:
    from ledger import GENESIS_HASH

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

#: Where the database lives. Override with the VERIGRADE_DB env var.
RAILWAY_VOLUME = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
if os.environ.get("VERIGRADE_DB"):
    DB_PATH = os.environ["VERIGRADE_DB"]
elif RAILWAY_VOLUME:
    DB_PATH = os.path.join(RAILWAY_VOLUME, "results.db")
else:
    DB_PATH = os.path.join(BASE_DIR, "results.db")

#: Column order used for every INSERT (kept in sync with SCHEMA_SQL).
COLUMNS = (
    "entry_type", "roll_no", "student_name", "subject", "marks", "max_marks", "exam_session",
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
    max_marks     INTEGER,
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

CREATE TRIGGER IF NOT EXISTS trg_ledger_publish_unique
BEFORE INSERT ON ledger
WHEN NEW.entry_type = 'PUBLISH'
 AND EXISTS (
     SELECT 1 FROM ledger
     WHERE entry_type = 'PUBLISH'
       AND lower(trim(roll_no)) = lower(trim(NEW.roll_no))
       AND lower(trim(subject)) = lower(trim(NEW.subject))
       AND lower(trim(exam_session)) = lower(trim(NEW.exam_session))
 )
BEGIN
    SELECT RAISE(ABORT, 'DUPLICATE_PUBLISH');
END;

CREATE TABLE IF NOT EXISTS ledger_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    created_at TEXT NOT NULL
);
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
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(ledger)").fetchall()}
        if "max_marks" not in columns:
            conn.execute("ALTER TABLE ledger ADD COLUMN max_marks INTEGER")
        migrate_hash_v2(conn)


def migrate_hash_v2(conn: sqlite3.Connection) -> None:
    """One-time migration that includes max_marks in every ledger hash."""
    migration = conn.execute(
        "SELECT value FROM ledger_metadata WHERE key = 'hash_payload_v2'"
    ).fetchone()
    if migration:
        return

    try:
        from .ledger import prepare_entry
    except ImportError:
        from ledger import prepare_entry
    rows = conn.execute("SELECT * FROM ledger ORDER BY id ASC").fetchall()
    prev_hash = GENESIS_HASH
    for row in rows:
        entry = dict(row)
        entry["max_marks"] = entry.get("max_marks") or 100
        prepared = prepare_entry(entry, prev_hash)
        conn.execute(
            "UPDATE ledger SET max_marks = ?, prev_hash = ?, record_hash = ? WHERE id = ?",
            (entry["max_marks"], prepared["prev_hash"], prepared["record_hash"], entry["id"]),
        )
        prev_hash = prepared["record_hash"]
    conn.execute(
        "INSERT OR REPLACE INTO ledger_metadata (key, value) VALUES ('hash_payload_v2', '1')"
    )


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


def append_entry(entry: Dict[str, Any], duplicate_key: Optional[tuple[str, str, str]] = None) -> int:
    """Atomically choose a tip and append one ledger entry with a write lock."""
    try:
        from .ledger import prepare_entry
    except ImportError:
        from ledger import prepare_entry

    placeholders = ", ".join("?" for _ in COLUMNS)
    sql = f"INSERT INTO ledger ({', '.join(COLUMNS)}) VALUES ({placeholders})"

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if duplicate_key is not None:
            roll_no, subject, exam_session = duplicate_key
            existing = conn.execute(
                "SELECT id FROM ledger "
                "WHERE entry_type = 'PUBLISH' "
                "AND lower(trim(roll_no)) = lower(trim(?)) "
                "AND lower(trim(subject)) = lower(trim(?)) "
                "AND lower(trim(exam_session)) = lower(trim(?)) "
                "LIMIT 1",
                (roll_no, subject, exam_session),
            ).fetchone()
            if existing:
                raise sqlite3.IntegrityError("DUPLICATE_PUBLISH")

        tip = conn.execute(
            "SELECT record_hash FROM ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = (
            tip["record_hash"]
            if tip and isinstance(tip["record_hash"], str) and tip["record_hash"]
            else GENESIS_HASH
        )
        prepared = prepare_entry(entry, prev_hash)
        cursor = conn.execute(
            sql,
            [prepared.get(column) for column in COLUMNS],
        )
        new_id = int(cursor.lastrowid)
        prepared["id"] = new_id
        entry.clear()
        entry.update(prepared)
        return new_id


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


def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, email, password_hash, role, created_at "
            "FROM users WHERE lower(email) = lower(?)",
            (email.strip(),),
        ).fetchone()
    return dict(row) if row else None


def find_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, email, password_hash, role, created_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def create_user(name: str, email: str, password_hash: str, role: str = "viewer") -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO users (name, email, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                name.strip(),
                email.strip().lower(),
                password_hash,
                role,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        user_id = int(cursor.lastrowid)
    return {"id": user_id, "name": name.strip(), "email": email.strip().lower(), "role": role}