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
USER_ROLES = {"viewer", "publisher", "approver", "admin"}

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

CREATE TABLE IF NOT EXISTS correction_requests (
    id TEXT PRIMARY KEY,
    target_id INTEGER NOT NULL REFERENCES ledger(id),
    original_marks INTEGER NOT NULL,
    requested_marks INTEGER NOT NULL,
    reason TEXT NOT NULL,
    requester_user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Approved', 'Rejected')),
    approver_1_user_id INTEGER REFERENCES users(id),
    approver_2_user_id INTEGER REFERENCES users(id),
    rejected_by_user_id INTEGER REFERENCES users(id),
    correction_entry_id INTEGER REFERENCES ledger(id),
    rejection_reason TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_correction_requests_status_created
    ON correction_requests (status, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_correction_requests_pending_target
    ON correction_requests (target_id) WHERE status = 'Pending';
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


def append_entry(
    entry: Dict[str, Any],
    duplicate_key: Optional[tuple[str, str, str]] = None,
    *,
    correction_request_id: Optional[str] = None,
    approver_user_ids: Optional[tuple[int, int]] = None,
) -> int:
    """Atomically choose a tip and append one ledger entry with a write lock."""
    try:
        from .ledger import prepare_entry
    except ImportError:
        from ledger import prepare_entry

    placeholders = ", ".join("?" for _ in COLUMNS)
    sql = f"INSERT INTO ledger ({', '.join(COLUMNS)}) VALUES ({placeholders})"

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        correction_request = None
        if correction_request_id:
            correction_request = conn.execute(
                "SELECT target_id, status FROM correction_requests WHERE id = ?",
                (correction_request_id,),
            ).fetchone()
            if not correction_request or correction_request["status"] != "Pending":
                raise sqlite3.IntegrityError("CORRECTION_REQUEST_NOT_PENDING")
            requested_target_id = int(correction_request["target_id"])
            if requested_target_id != int(entry.get("supersedes_id") or 0):
                raise sqlite3.IntegrityError("STALE_CORRECTION_REQUEST")
            target = conn.execute(
                "SELECT entry_type, target_id FROM ledger WHERE id = ?",
                (requested_target_id,),
            ).fetchone()
            if not target:
                raise sqlite3.IntegrityError("STALE_CORRECTION_REQUEST")
            publish_id = (
                int(target["target_id"])
                if target["entry_type"] == "CORRECTION"
                else requested_target_id
            )
            latest_correction = conn.execute(
                "SELECT id FROM ledger "
                "WHERE entry_type = 'CORRECTION' AND target_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (publish_id,),
            ).fetchone()
            current_id = int(latest_correction["id"]) if latest_correction else publish_id
            if current_id != requested_target_id:
                raise sqlite3.IntegrityError("STALE_CORRECTION_REQUEST")
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
        if correction_request_id:
            if not approver_user_ids or len(approver_user_ids) != 2 or approver_user_ids[0] == approver_user_ids[1]:
                raise sqlite3.IntegrityError("APPROVERS_NOT_DISTINCT")
            updated = conn.execute(
                "UPDATE correction_requests SET status = 'Approved', "
                "approver_1_user_id = ?, approver_2_user_id = ?, "
                "correction_entry_id = ?, decided_at = ? "
                "WHERE id = ? AND status = 'Pending'",
                (
                    approver_user_ids[0],
                    approver_user_ids[1],
                    new_id,
                    entry.get("created_at"),
                    correction_request_id,
                ),
            )
            if updated.rowcount != 1:
                raise sqlite3.IntegrityError("CORRECTION_REQUEST_NOT_PENDING")
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


def list_users() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, email, role, created_at FROM users ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def update_user_role(user_id: int, role: str) -> Optional[Dict[str, Any]]:
    if role not in USER_ROLES:
        raise ValueError("Invalid user role.")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not current:
            return None
        if current["role"] == "admin" and role != "admin":
            admin_count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin'"
            ).fetchone()[0]
            if admin_count <= 1:
                raise ValueError("The last administrator cannot be demoted.")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        updated = conn.execute(
            "SELECT id, name, email, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(updated)


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


CORRECTION_REQUEST_SELECT = """
SELECT cr.*,
       requester.name AS requested_by_name,
       first_approver.name AS approver_1_name,
       second_approver.name AS approver_2_name,
       rejector.name AS rejected_by_name,
       target.roll_no AS roll_no,
       target.subject AS subject
FROM correction_requests cr
JOIN users requester ON requester.id = cr.requester_user_id
JOIN ledger target ON target.id = cr.target_id
LEFT JOIN users first_approver ON first_approver.id = cr.approver_1_user_id
LEFT JOIN users second_approver ON second_approver.id = cr.approver_2_user_id
LEFT JOIN users rejector ON rejector.id = cr.rejected_by_user_id
"""


def create_correction_request(
    request_id: str,
    target_id: int,
    original_marks: int,
    requested_marks: int,
    reason: str,
    requester_user_id: int,
    created_at: str,
) -> Dict[str, Any]:
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO correction_requests "
            "(id, target_id, original_marks, requested_marks, reason, requester_user_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                target_id,
                original_marks,
                requested_marks,
                reason,
                requester_user_id,
                created_at,
            ),
        )
        row = conn.execute(
            CORRECTION_REQUEST_SELECT + " WHERE cr.id = ?", (request_id,)
        ).fetchone()
    return dict(row)


def fetch_correction_request(request_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            CORRECTION_REQUEST_SELECT + " WHERE cr.id = ?", (request_id,)
        ).fetchone()
    return dict(row) if row else None


def list_correction_requests() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            CORRECTION_REQUEST_SELECT + " ORDER BY cr.created_at DESC, cr.id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def reject_correction_request(
    request_id: str,
    approver_user_id: int,
    reason: str,
    decided_at: str,
) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute(
            "UPDATE correction_requests SET status = 'Rejected', "
            "rejected_by_user_id = ?, rejection_reason = ?, decided_at = ? "
            "WHERE id = ? AND status = 'Pending'",
            (approver_user_id, reason, decided_at, request_id),
        )
        if updated.rowcount != 1:
            return None
        row = conn.execute(
            CORRECTION_REQUEST_SELECT + " WHERE cr.id = ?", (request_id,)
        ).fetchone()
    return dict(row) if row else None