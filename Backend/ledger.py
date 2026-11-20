"""
ledger.py
=========
The cryptographic heart of VeriGrade.

Every entry appended to the ledger is hashed with SHA-256 over a *canonical*
JSON representation of its contents (keys sorted, no whitespace) **plus** the
hash of the entry that came before it (``prev_hash``). Because every hash
commits to the previous hash, the entries form a tamper-evident chain:
change a single mark anywhere in the SQLite file and every hash afterwards
stops matching.

This module deliberately contains **no database code**. It turns a dict of
fields into a hash, and it audits a list of such dicts. That separation keeps
it easy to test and easy to explain in a viva.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: The "block 0" of the chain. Every first record commits to this value, so a
#: deleted first record is still detectable (the next record's prev_hash will
#: no longer match the genesis hash).
GENESIS_HASH = hashlib.sha256(b"VERIGRADE-GENESIS-BLOCK-v1").hexdigest()

#: Tag written inside every QR payload so foreign QR codes are rejected fast.
QR_TAG = "VERIGRADE1"

#: Fields that participate in the hash. Order does not matter (canonical JSON
#: sorts keys) but the *set* must never change for an existing database.
HASHED_FIELDS = (
    "entry_type",
    "roll_no",
    "student_name",
    "subject",
    "marks",
    "exam_session",
    "target_id",
    "supersedes_id",
    "reason",
    "approver_1",
    "approver_2",
    "created_at",
    "prev_hash",
)

#: (field name, maximum length) for the free-text columns.
REQUIRED_TEXT_FIELDS = (
    ("roll_no", 40),
    ("student_name", 80),
    ("subject", 60),
    ("exam_session", 30),
)

STATUS_RANK = {"VERIFIED": 0, "TAMPERED": 1, "CORRUPTED": 2}

_HEX_DIGITS = set("0123456789abcdef")


class VerificationInputError(ValueError):
    """Raised when a verification request cannot be understood."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def utc_now_iso() -> str:
    """Return a deterministic, sortable UTC timestamp (microsecond precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def is_sha256_hex(value) -> bool:
    """True when *value* looks exactly like a lowercase SHA-256 hex digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in _HEX_DIGITS for ch in value)
    )


def _safe(value):
    """Make any SQLite value JSON-serialisable (BLOBs, etc. become repr text)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _as_int(value):
    """Best-effort integer conversion; returns None instead of raising."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _short(value, keep: int = 12) -> str:
    """Abbreviate a hash for human-readable messages."""
    text = "" if value is None else str(value)
    return text[:keep] + "…" if len(text) > keep else text


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

def build_hash_payload(entry: dict) -> dict:
    """
    Build the exact dict that gets hashed.

    Numbers are normalised so ``88`` (int) and ``"88"`` (string read back from
    SQLite after an odd edit) produce the same canonical payload. Every other
    field is coerced to text (or ``None``) so JSON serialisation is stable.
    """
    payload = {}
    for field in HASHED_FIELDS:
        raw = entry.get(field)
        if field == "marks":
            marks = _as_int(raw)
            payload[field] = marks  # may be None -> validation will flag it
        elif field in ("target_id", "supersedes_id"):
            payload[field] = None if raw in (None, "") else _as_int(raw)
        else:
            payload[field] = None if raw is None else str(raw)
    return payload


def canonical_json(payload: dict) -> str:
    """Canonical JSON: sorted keys, no whitespace, UTF-8 friendly."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_record_hash(entry: dict) -> str:
    """
    SHA-256 over ``canonical_json(payload)``.

    Because ``prev_hash`` is one of the hashed fields, this single digest
    commits both to the record's own data **and** to the entire history
    before it.
    """
    payload = build_hash_payload(entry)
    if payload["marks"] is None:
        raise ValueError("marks is not numeric — cannot compute a hash")
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def prepare_entry(entry: dict, prev_hash: str) -> dict:
    """Attach ``prev_hash``, ``created_at`` and the computed ``record_hash``."""
    prepared = dict(entry)
    prepared["prev_hash"] = prev_hash
    if not prepared.get("created_at"):
        prepared["created_at"] = utc_now_iso()
    prepared["record_hash"] = compute_record_hash(prepared)
    return prepared


def build_qr_payload(entry: dict) -> str:
    """Compact deterministic QR payload: tag + entry id + record hash."""
    payload = {"t": QR_TAG, "id": int(entry["id"]), "h": entry["record_hash"]}
    return json.dumps(payload, separators=(",", ":"))


def parse_qr_payload(raw) -> tuple[int, str]:
    """
    Validate a QR payload string. Raises ``VerificationInputError`` with a
    friendly, user-facing explanation on any problem (never a 500).
    """
    if not isinstance(raw, str):
        raise VerificationInputError("QR payload must be text.")
    text = raw.strip()
    if not text:
        raise VerificationInputError("the QR payload is empty.")
    if len(text) > 600:
        raise VerificationInputError("the QR payload is unreasonably long.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise VerificationInputError("it is not valid JSON.")
    if not isinstance(data, dict):
        raise VerificationInputError("it is not a JSON object.")
    if data.get("t") != QR_TAG:
        raise VerificationInputError(f"it is missing the '{QR_TAG}' tag.")
    entry_id = data.get("id")
    if isinstance(entry_id, bool) or not isinstance(entry_id, int) or entry_id < 1:
        raise VerificationInputError("it has no valid record id.")
    record_hash = data.get("h")
    if not is_sha256_hex(record_hash):
        raise VerificationInputError("it has no valid 64-hex SHA-256 hash.")
    return entry_id, record_hash.lower()


# --------------------------------------------------------------------------
# Validation of a stored row
# --------------------------------------------------------------------------

def validate_marks(value):
    """
    Normalise a marks value coming either from JSON input or from SQLite.

    Returns ``(marks:int|None, problem:str|None)``. This is the single place
    where "marks must be an integer between 0 and 100" is enforced, so a row
    edited to ``NULL``, ``'abc'`` or ``150`` is reported as CORRUPTED rather
    than crashing the audit.
    """
    if isinstance(value, bool):
        return None, "marks must be a number, not a boolean"
    if isinstance(value, int):
        marks = value
    elif isinstance(value, float) and value.is_integer():
        marks = int(value)
    elif isinstance(value, str):
        try:
            marks = int(value.strip())
        except ValueError:
            return None, f"marks is not numeric (found {value!r})"
    else:
        return None, f"marks is not numeric (found {type(value).__name__})"

    if not 0 <= marks <= 100:
        return None, f"marks {marks} is outside the allowed range 0-100"
    return marks, None


def validate_entry(entry: dict) -> list[str]:
    """
    Return a list of structural problems with one ledger row.

    An empty list means the row is *readable* — it does not yet mean the hash
    is correct (that is a separate check in :func:`audit_chain`).
    """
    problems: list[str] = []
    if not isinstance(entry, dict):
        return ["row is not a dictionary"]

    entry_type = entry.get("entry_type")
    if entry_type not in ("PUBLISH", "CORRECTION"):
        problems.append(f"entry_type must be PUBLISH or CORRECTION (found {entry_type!r})")

    for field, max_len in REQUIRED_TEXT_FIELDS:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"'{field}' is missing, NULL or blank")
        elif len(value.strip()) > max_len:
            problems.append(f"'{field}' is longer than {max_len} characters")

    _, marks_problem = validate_marks(entry.get("marks"))
    if marks_problem:
        problems.append(marks_problem)

    created_at = entry.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        problems.append("'created_at' is missing or NULL")

    for field in ("prev_hash", "record_hash"):
        if not is_sha256_hex(entry.get(field)):
            problems.append(f"'{field}' is missing or not a 64-hex SHA-256 digest")

    if entry_type == "CORRECTION":
        if _as_int(entry.get("target_id")) is None:
            problems.append("a correction must reference the original record via 'target_id'")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append("a correction must carry an approval 'reason'")
        approver_1 = entry.get("approver_1")
        approver_2 = entry.get("approver_2")
        a1 = approver_1.strip().lower() if isinstance(approver_1, str) else ""
        a2 = approver_2.strip().lower() if isinstance(approver_2, str) else ""
        if not a1 or not a2:
            problems.append("a correction must carry two approver IDs")
        elif a1 == a2:
            problems.append("the two approvers must be different people")

    return problems


# --------------------------------------------------------------------------
# The audit engine
# --------------------------------------------------------------------------

def _worst_status(statuses) -> str:
    """Return the most severe status in *statuses* (CORRUPTED > TAMPERED > VERIFIED)."""
    worst = "VERIFIED"
    for status in statuses:
        if STATUS_RANK.get(status, 0) > STATUS_RANK.get(worst, 0):
            worst = status
    return worst


def audit_chain(entries: list[dict]) -> dict:
    """
    Recompute every record's SHA-256 from its CURRENT database values and
    verify that each ``prev_hash`` links to the previous entry.

    Returns ``{"entries": [...enriched rows...], "summary": {...}}``.

    Status per row:
      * ``VERIFIED``  – fields valid, hash matches, link matches
      * ``TAMPERED``  – fields valid but the hash or the link does not match
      * ``CORRUPTED`` – fields are missing / NULL / unreadable
    """
    audited: list[dict] = []
    expected_prev = GENESIS_HASH  # what this row's prev_hash *should* be
    summary = {
        "total": len(entries),
        "verified": 0,
        "tampered": 0,
        "corrupted": 0,
        "chain_intact": True,
        "first_problem": None,
        "genesis_hash": GENESIS_HASH,
        "head_hash": entries[-1].get("record_hash") if entries else GENESIS_HASH,
        "audited_at": utc_now_iso(),
    }

    for position, entry in enumerate(entries, start=1):
        problems = validate_entry(entry)

        # Try to recompute the hash. Only possible when the fields are readable.
        recomputed = None
        if not problems:
            try:
                recomputed = compute_record_hash(entry)
            except (TypeError, ValueError) as exc:  # defensive: never 500
                problems.append(f"record could not be hashed: {exc}")

        stored_hash = entry.get("record_hash")
        link_ok = expected_prev is not None and entry.get("prev_hash") == expected_prev

        reasons = list(problems)
        if problems:
            status = "CORRUPTED"
        elif recomputed != stored_hash:
            status = "TAMPERED"
            reasons.append(
                f"content hash mismatch — stored {_short(stored_hash)} but recomputed "
                f"{_short(recomputed)} from the current values"
            )
        elif not link_ok:
            status = "TAMPERED"
            if expected_prev is None:
                reasons.append(
                    "chain link is unprovable because an earlier entry failed its hash check"
                )
            else:
                reasons.append(
                    f"prev_hash {_short(entry.get('prev_hash'))} does not match the previous "
                    f"entry's real hash {_short(expected_prev)}"
                )
        else:
            status = "VERIFIED"

        audited.append({
            "id": _safe(entry.get("id")),
            "position": position,
            "entry_type": _safe(entry.get("entry_type")),
            "roll_no": _safe(entry.get("roll_no")),
            "student_name": _safe(entry.get("student_name")),
            "subject": _safe(entry.get("subject")),
            "marks": _safe(entry.get("marks")),
            "exam_session": _safe(entry.get("exam_session")),
            "target_id": _safe(entry.get("target_id")),
            "supersedes_id": _safe(entry.get("supersedes_id")),
            "reason": _safe(entry.get("reason")),
            "approver_1": _safe(entry.get("approver_1")),
            "approver_2": _safe(entry.get("approver_2")),
            "created_at": _safe(entry.get("created_at")),
            "prev_hash": _safe(entry.get("prev_hash")),
            "record_hash": _safe(stored_hash),
            "recomputed_hash": _safe(recomputed),
            "status": status,
            "link_status": "OK" if link_ok else "BROKEN",
            "reasons": reasons,
        })

        if status == "VERIFIED":
            summary["verified"] += 1
        elif status == "TAMPERED":
            summary["tampered"] += 1
        else:
            summary["corrupted"] += 1

        if status != "VERIFIED":
            summary["chain_intact"] = False
            if summary["first_problem"] is None:
                summary["first_problem"] = {
                    "id": _safe(entry.get("id")),
                    "status": status,
                    "reasons": reasons,
                }

        # The *real* hash becomes the reference for the next row. If this row
        # could not be hashed, everything downstream is unprovable.
        expected_prev = recomputed

    return {"entries": audited, "summary": summary}


# --------------------------------------------------------------------------
# Effective (current) record resolution
# --------------------------------------------------------------------------

def _find_entry(items: list[dict], entry_id):
    """Return the audited item with the given id, or None."""
    wanted = _as_int(entry_id)
    if wanted is None:
        return None
    for item in items:
        if _as_int(item.get("id")) == wanted:
            return item
    return None


def group_for(items: list[dict], publisher: dict) -> dict:
    """Collect the publish entry plus every correction that targets it."""
    publish_id = _as_int(publisher.get("id"))
    corrections = [
        item for item in items
        if item.get("entry_type") == "CORRECTION"
        and _as_int(item.get("target_id")) == publish_id
    ]
    corrections.sort(key=lambda item: _as_int(item.get("id")) or 0)
    return {
        "publish": publisher,
        "corrections": corrections,
        "effective": corrections[-1] if corrections else publisher,
    }


def build_effective_view(items: list[dict]) -> list[dict]:
    """
    Turn the raw chain into a list of *current* results: one row per published
    result, with the latest correction (if any) applied on top.
    """
    publishers = [
        item for item in items
        if item.get("entry_type") == "PUBLISH"
        and _as_int(item.get("id")) is not None
    ]
    view = []
    seen_keys = {}

    for publisher in publishers:
        group = group_for(items, publisher)
        effective = group["effective"]
        members = [group["publish"]] + group["corrections"]

        key = (
            str(effective.get("roll_no") or "").strip().lower(),
            str(effective.get("subject") or "").strip().lower(),
            str(effective.get("exam_session") or "").strip().lower(),
        )
        seen_keys[key] = seen_keys.get(key, 0) + 1

        view.append({
            "publish_id": _as_int(publisher.get("id")),
            "record": describe_entry(effective),
            "original": describe_entry(group["publish"]),
            "corrections": [describe_entry(item) for item in group["corrections"]],
            "status": _worst_status([member.get("status") for member in members]),
            "reasons": [r for member in members for r in (member.get("reasons") or [])],
            "_key": key,
        })

    for row in view:
        row["duplicate_key"] = seen_keys.get(row["_key"], 0) > 1
        row.pop("_key", None)

    view.sort(key=lambda row: (
        str(row["record"].get("roll_no") or "").lower(),
        str(row["record"].get("subject") or "").lower(),
    ))
    return view


def describe_entry(entry: dict) -> dict:
    """Return a JSON-safe, public view of one ledger entry."""
    if not isinstance(entry, dict):
        return {}
    return {
        "id": _safe(entry.get("id")),
        "entry_type": _safe(entry.get("entry_type")),
        "roll_no": _safe(entry.get("roll_no")),
        "student_name": _safe(entry.get("student_name")),
        "subject": _safe(entry.get("subject")),
        "marks": _safe(entry.get("marks")),
        "exam_session": _safe(entry.get("exam_session")),
        "target_id": _safe(entry.get("target_id")),
        "supersedes_id": _safe(entry.get("supersedes_id")),
        "reason": _safe(entry.get("reason")),
        "approver_1": _safe(entry.get("approver_1")),
        "approver_2": _safe(entry.get("approver_2")),
        "prev_hash": _safe(entry.get("prev_hash")),
        "record_hash": _safe(entry.get("record_hash")),
        "created_at": _safe(entry.get("created_at")),
        "status": _safe(entry.get("status")),
        "link_status": _safe(entry.get("link_status")),
        "reasons": entry.get("reasons") or [],
    }


# --------------------------------------------------------------------------
# Verification orchestration
# --------------------------------------------------------------------------

def _matches_key(item: dict, roll_no: str, subject: str, session: str) -> bool:
    """Case-insensitive match on (roll_no, subject[, session]) for PUBLISH rows."""
    if item.get("entry_type") != "PUBLISH":
        return False
    item_roll = item.get("roll_no")
    item_subject = item.get("subject")
    if not isinstance(item_roll, str) or not isinstance(item_subject, str):
        return False
    if item_roll.strip().lower() != roll_no.strip().lower():
        return False
    if item_subject.strip().lower() != subject.strip().lower():
        return False
    if session:
        item_session = item.get("exam_session")
        if not isinstance(item_session, str):
            return False
        if item_session.strip().lower() != session.strip().lower():
            return False
    return True


def _corrupted_note(items: list[dict]) -> str:
    """Mention unreadable rows so a 'not found' is never misleading."""
    count = sum(1 for item in items if item.get("status") == "CORRUPTED")
    if not count:
        return ""
    return (f" Note: {count} ledger row(s) are CORRUPTED (missing/NULL fields) and "
            "could not be searched.")


def _invalid(query, message, summary, checked_at) -> dict:
    """Build a well-formed INVALID response."""
    return {
        "ok": True,
        "status": "INVALID",
        "message": message,
        "reasons": [message],
        "query": query,
        "record": None,
        "original": None,
        "history": [],
        "proof": None,
        "chain": summary,
        "checked_at": checked_at,
    }


def _verification(items, summary, query, target, checked_at, expected_hash=None) -> dict:
    """Build the full proof response for the entry *target*."""
    # 1. Resolve the root PUBLISH entry -------------------------------------
    root = target
    if target.get("entry_type") == "CORRECTION":
        root = _find_entry(items, target.get("target_id"))
        if root is None:
            return _invalid(
                query,
                f"Entry #{target.get('id')} is a correction whose original record no longer exists.",
                summary,
                checked_at,
            )

    group = group_for(items, root)
    publisher = group["publish"]
    corrections = group["corrections"]
    effective = group["effective"]
    members = [publisher] + corrections

    # 2. Worst status across the record and its whole correction history ----
    status = _worst_status([member.get("status") for member in members])
    extra_messages = []

    # 3. If the caller supplied a hash (QR mode), it must match what is stored
    if expected_hash is not None:
        stored = str(target.get("record_hash") or "").lower()
        if expected_hash.lower() != stored:
            status = "TAMPERED"
            extra_messages.append(
                "The SHA-256 hash inside the QR payload does not match the hash stored for "
                f"entry #{target.get('id')} — the QR code is stale, or it was forged."
            )

    if status == "VERIFIED":
        message = (
            "VERIFIED — the stored hash equals a fresh SHA-256 recomputation of this record, "
            "and its prev_hash links cleanly to the entry before it."
        )
    elif status == "TAMPERED":
        message = (
            "TAMPERED — the SHA-256 chain does not match this record. One or more fields "
            "(for example the marks) were changed after publication."
        )
    else:
        message = (
            "CORRUPTED — this record contains missing, NULL or unreadable fields, so the "
            "ledger cannot prove it."
        )
    if extra_messages:
        message = message + " " + " ".join(extra_messages)

    reasons: list[str] = list(extra_messages)
    for member in members:
        for reason in member.get("reasons") or []:
            if reason not in reasons:
                reasons.append(reason)

    record = describe_entry(effective)
    original = describe_entry(publisher)
    history = []
    for correction in corrections:
        item = describe_entry(correction)
        item["status"] = _safe(correction.get("status"))
        item["link_status"] = _safe(correction.get("link_status"))
        history.append(item)

    proof = {
        "entry_id": _safe(effective.get("id")),
        "position": effective.get("position"),
        "record_hash": _safe(effective.get("record_hash")),
        "recomputed_hash": _safe(effective.get("recomputed_hash")),
        "prev_hash": _safe(effective.get("prev_hash")),
        "genesis_hash": GENESIS_HASH,
        "link_status": _safe(effective.get("link_status")),
        "chain_length": summary.get("total"),
        "chain_intact": summary.get("chain_intact"),
        "verified_at": checked_at,
    }

    return {
        "ok": True,
        "status": status,
        "message": message,
        "reasons": reasons,
        "query": query,
        "record": record,
        "original": original,
        "history": history,
        "proof": proof,
        "chain": summary,
        "checked_at": checked_at,
    }


def run_verification(items, summary, query, mode, value) -> dict:
    """
    Verify by ``id``, ``roll``, ``hash`` or ``qr``.

    Always returns a dict with a ``status`` of VERIFIED / TAMPERED / CORRUPTED /
    INVALID — never raises for user input problems.
    """
    checked_at = utc_now_iso()

    if mode == "qr":
        try:
            qr_id, qr_hash = parse_qr_payload(value)
        except VerificationInputError as exc:
            return _invalid(
                query, f"This QR code is not a valid VeriGrade payload — {exc}", summary, checked_at
            )
        target = _find_entry(items, qr_id)
        if target is None:
            return _invalid(
                query,
                f"The QR code points to record #{qr_id}, which does not exist in this ledger.",
                summary, checked_at,
            )
        return _verification(items, summary, query, target, checked_at, expected_hash=qr_hash)

    if mode == "id":
        target = _find_entry(items, value)
        if target is None:
            return _invalid(query, f"No ledger entry with id #{value} exists.", summary, checked_at)
        return _verification(items, summary, query, target, checked_at)

    if mode == "hash":
        wanted = str(value).strip().lower()
        matches = [
            item for item in items
            if str(item.get("record_hash") or "").lower() == wanted
            or str(item.get("recomputed_hash") or "").lower() == wanted
        ]
        if not matches:
            return _invalid(query, "No ledger entry matches that SHA-256 hash.", summary, checked_at)
        return _verification(items, summary, query, matches[0], checked_at, expected_hash=wanted)

    if mode == "roll":
        roll_no = str(value.get("roll_no", "") or "").strip()
        subject = str(value.get("subject", "") or "").strip()
        session = str(value.get("exam_session", "") or "").strip()
        if not roll_no or not subject:
            return _invalid(query, "Roll number and subject are both required.", summary, checked_at)
        matches = [item for item in items if _matches_key(item, roll_no, subject, session)]
        if not matches:
            return _invalid(
                query,
                f"No published result found for roll '{roll_no}' in subject '{subject}'"
                + (f" ({session})" if session else "")
                + "." + _corrupted_note(items),
                summary, checked_at,
            )
        matches.sort(key=lambda item: _as_int(item.get("id")) or 0)
        result = _verification(items, summary, query, matches[-1], checked_at)
        if len(matches) > 1:
            result["message"] += (
                f" Warning: {len(matches)} PUBLISH entries share this key — the ledger "
                "contains forged duplicates."
            )
        return result

    return _invalid(query, f"Unknown verification mode '{mode}'.", summary, checked_at)