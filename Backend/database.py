"""Database persistence for SQLite development and PostgreSQL deployments."""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

try:
    from .db_config import database_url, is_sqlite_url, sqlite_path
    from .ledger import GENESIS_HASH
except ImportError:
    from db_config import database_url, is_sqlite_url, sqlite_path
    from ledger import GENESIS_HASH

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = database_url()
IS_SQLITE = is_sqlite_url(DATABASE_URL)
DB_PATH = sqlite_path() if IS_SQLITE else None
INTEGRITY_ERRORS = (
    sqlite3.IntegrityError,
    psycopg.IntegrityError,
    SQLAlchemyIntegrityError,
)

#: Column order used for every ledger INSERT.
COLUMNS = (
    "entry_type", "roll_no", "student_name", "subject", "marks", "max_marks", "exam_session",
    "target_id", "supersedes_id", "reason", "approver_1", "approver_2",
    "prev_hash", "record_hash", "created_at",
)
USER_ROLES = {"viewer", "publisher", "approver", "admin"}

engine_options = {"pool_pre_ping": True, "hide_parameters": True}
if IS_SQLITE:
    engine_options["connect_args"] = {"timeout": 15, "check_same_thread": False}
engine = create_engine(DATABASE_URL, **engine_options)

if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@contextmanager
def get_connection():
    """Yield a dialect-neutral connection and commit or roll back atomically."""
    if IS_SQLITE and DB_PATH:
        directory = os.path.dirname(DB_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
    with engine.begin() as connection:
        yield ConnectionAdapter(connection)


class ResultAdapter:
    def __init__(self, result):
        self._result = result.mappings()
        self.rowcount = result.rowcount

    def fetchone(self):
        return self._result.fetchone()

    def fetchall(self):
        return self._result.fetchall()


class ConnectionAdapter:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, statement, params=()):
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            self._connection.exec_driver_sql(statement)
            return ResultAdapter(self._connection.exec_driver_sql("SELECT 1"))
        placeholders = []

        def bind_placeholder(_match):
            name = "p" + str(len(placeholders))
            placeholders.append(name)
            return ":" + name

        converted = re.sub(r"\?", bind_placeholder, statement)
        values = dict(zip(placeholders, params)) if placeholders else {}
        return ResultAdapter(self._connection.execute(text(converted), values))


def begin_write_transaction(conn: ConnectionAdapter) -> None:
    if IS_SQLITE:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("SELECT pg_advisory_xact_lock(?)", (741009812,))


def raise_integrity_error(message: str) -> None:
    error_type = sqlite3.IntegrityError if IS_SQLITE else psycopg.IntegrityError
    raise error_type(message)


def init_db() -> None:
    """Apply schema migrations and any remaining one-time data migrations."""
    config = Config(os.path.join(BASE_DIR, "..", "alembic.ini"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    command.upgrade(config, "head")
    migrate_hash_v2()


def migrate_hash_v2() -> None:
    """One-time migration that includes max_marks in every ledger hash."""
    try:
        from .ledger import prepare_entry
    except ImportError:
        from ledger import prepare_entry
    with get_connection() as conn:
        migration = conn.execute(
            "SELECT value FROM ledger_metadata WHERE key = 'hash_payload_v2'"
        ).fetchone()
        if migration:
            return
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
            "INSERT INTO ledger_metadata (key, value) VALUES ('hash_payload_v2', '1') "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value"
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
    sql = f"INSERT INTO ledger ({', '.join(COLUMNS)}) VALUES ({placeholders}) RETURNING id"
    values = [entry.get(column) for column in COLUMNS]
    with get_connection() as conn:
        row = conn.execute(sql, values).fetchone()
        new_id = row["id"]
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
    sql = f"INSERT INTO ledger ({', '.join(COLUMNS)}) VALUES ({placeholders}) RETURNING id"

    with get_connection() as conn:
        begin_write_transaction(conn)
        correction_request = None
        if correction_request_id:
            correction_request = conn.execute(
                "SELECT target_id, status FROM correction_requests WHERE id = ?",
                (correction_request_id,),
            ).fetchone()
            if not correction_request or correction_request["status"] != "Pending":
                raise_integrity_error("CORRECTION_REQUEST_NOT_PENDING")
            requested_target_id = int(correction_request["target_id"])
            if requested_target_id != int(entry.get("supersedes_id") or 0):
                raise_integrity_error("STALE_CORRECTION_REQUEST")
            target = conn.execute(
                "SELECT entry_type, target_id FROM ledger WHERE id = ?",
                (requested_target_id,),
            ).fetchone()
            if not target:
                raise_integrity_error("STALE_CORRECTION_REQUEST")
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
                raise_integrity_error("STALE_CORRECTION_REQUEST")
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
                raise_integrity_error("DUPLICATE_PUBLISH")

        tip = conn.execute(
            "SELECT record_hash FROM ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = (
            tip["record_hash"]
            if tip and isinstance(tip["record_hash"], str) and tip["record_hash"]
            else GENESIS_HASH
        )
        prepared = prepare_entry(entry, prev_hash)
        inserted = conn.execute(
            sql,
            [prepared.get(column) for column in COLUMNS],
        ).fetchone()
        new_id = int(inserted["id"])
        if correction_request_id:
            if not approver_user_ids or len(approver_user_ids) != 2 or approver_user_ids[0] == approver_user_ids[1]:
                raise_integrity_error("APPROVERS_NOT_DISTINCT")
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
                raise_integrity_error("CORRECTION_REQUEST_NOT_PENDING")
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
        begin_write_transaction(conn)
        current = conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not current:
            return None
        if current["role"] == "admin" and role != "admin":
            admin_count = conn.execute(
                "SELECT COUNT(*) AS admin_count FROM users WHERE role = 'admin'"
            ).fetchone()["admin_count"]
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
        row = conn.execute(
            "INSERT INTO users (name, email, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (
                name.strip(),
                email.strip().lower(),
                password_hash,
                role,
                datetime.now(timezone.utc).isoformat(),
            ),
        ).fetchone()
        user_id = int(row["id"])
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
        begin_write_transaction(conn)
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
        begin_write_transaction(conn)
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