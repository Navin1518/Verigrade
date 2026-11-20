"""
app.py
======
Flask HTTP API for VeriGrade — Tamper-Proof Exam Result Ledger.

Run from the repository root:

    pip install -r Backend/requirements.txt
    python Backend/app.py

The server listens on http://127.0.0.1:5000 and serves the repository root,
so opening http://127.0.0.1:5000 in a browser is the easiest way to use the app.

Every route returns JSON shaped like {"ok": true|false, ...}. User mistakes are
reported with a clear message and the right HTTP status — never with a 500.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    from . import database as db
    from . import ledger
except ImportError:
    import database as db
    import ledger

# --------------------------------------------------------------------------
# App setup
# --------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))

app = Flask(__name__, static_folder=None)          # we serve the frontend manually
CORS(app, resources={r"/api/*": {"origins": "*"}})  # lets file:// and other ports call us

db.init_db()  # create results.db + schema if missing (persistence across restarts)

DEFAULT_SESSION = "2025-2026"
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


# --------------------------------------------------------------------------
# Response helpers
# --------------------------------------------------------------------------

def api_ok(payload: dict | None = None, status: int = 200):
    """Return a success envelope."""
    body = {"ok": True}
    if payload:
        body.update(payload)
    return jsonify(body), status


def api_error(message: str, code: str = "BAD_REQUEST", status: int = 400, **extra):
    """Return a consistent error envelope with a machine-readable code."""
    body = {"ok": False, "error": message, "code": code}
    body.update(extra)
    return jsonify(body), status


def read_json_body():
    """
    Parse the request body as JSON, tolerating a missing Content-Type.

    Returns ``(data, error_message)``. This is the only place that can produce
    a "malformed JSON" error, so every route can trust the rest.
    """
    data = request.get_json(silent=True)
    if data is not None:
        return data, None
    raw = (request.get_data(as_text=True) or "").strip()
    if not raw:
        return None, "Request body is empty — send a JSON object."
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return None, "Request body is not valid JSON."


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------

def clean_text(raw, field: str, max_len: int, *, required: bool = True, default=None):
    """
    Normalise one text field.

    Returns ``(value, problem)``. Names and subjects get their internal
    whitespace collapsed so ``"Aarav   Mehta"`` and ``"Aarav Mehta"`` are the
    same person in the duplicate check.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            return None, f"'{field}' is required and cannot be blank."
        return default, None
    if not isinstance(raw, str):
        return None, f"'{field}' must be text."
    value = raw.strip()
    if CONTROL_CHARS.search(value):
        return None, f"'{field}' contains control characters."
    if field in ("student_name", "subject"):
        value = " ".join(value.split())
    if len(value) > max_len:
        return None, f"'{field}' must be {max_len} characters or fewer."
    return value, None


def clean_marks(raw, field: str = "marks"):
    """Validate a marks value from JSON. Returns ``(marks, problem)``."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, f"'{field}' is required."
    marks, problem = ledger.validate_marks(raw)
    if problem:
        return None, f"'{field}' is invalid: {problem}."
    return marks, None


def parse_result_payload(payload):
    """
    Validate a publish request body.

    Returns ``(clean_dict, problems)``. ``problems`` is a list of readable
    messages so the UI can show all of them at once.
    """
    if not isinstance(payload, dict):
        return None, ["Request body must be a JSON object."]

    problems: list[str] = []
    clean: dict = {}

    for field, max_len in (
        ("roll_no", 40),
        ("student_name", 80),
        ("subject", 60),
    ):
        value, problem = clean_text(payload, field, max_len) if False else clean_text(
            payload.get(field), field, max_len
        )
        if problem:
            problems.append(problem)
        else:
            clean[field] = value

    session, problem = clean_text(
        payload.get("exam_session"), "exam_session", 30,
        required=False, default=DEFAULT_SESSION,
    )
    if problem:
        problems.append(problem)
    else:
        clean["exam_session"] = session or DEFAULT_SESSION

    marks, problem = clean_marks(payload.get("marks"), "marks")
    if problem:
        problems.append(problem)
    else:
        clean["marks"] = marks

    if problems:
        return None, problems
    return clean, []


def parse_verify_request(body):
    """
    Work out *what* the caller wants to verify.

    Supported shapes (any one of them):
        {"record_id": 3}
        {"roll_no": "...", "subject": "...", "exam_session": "..."}   # session optional
        {"hash": "<64 hex>"}
        {"qr_payload": "<json string>"}    (or a raw JSON string body)

    Returns ``(query_dict, mode, value)`` or raises ``VerificationInputError``.
    """
    if isinstance(body, str):
        return {"mode": "qr", "qr_payload": body}, "qr", body

    if not isinstance(body, dict):
        raise ledger.VerificationInputError(
            "Send a JSON object describing what to verify."
        )

    qr_value = body.get("qr_payload") or body.get("qr")
    if isinstance(qr_value, str):
        return {"mode": "qr", "qr_payload": qr_value}, "qr", qr_value

    hash_value = body.get("hash") or body.get("sha256")
    if isinstance(hash_value, str) and hash_value.strip():
        hash_value = hash_value.strip()
        return {"mode": "hash", "hash": hash_value}, "hash", hash_value

    id_value = body.get("record_id", body.get("id"))
    if id_value is not None:
        try:
            entry_id = int(str(id_value).strip())
        except (TypeError, ValueError):
            raise ledger.VerificationInputError("'record_id' must be a whole number.")
        if entry_id < 1:
            raise ledger.VerificationInputError("'record_id' must be 1 or greater.")
        return {"mode": "id", "record_id": entry_id}, "id", entry_id

    roll_no = body.get("roll_no")
    subject = body.get("subject")
    if isinstance(roll_no, str) and roll_no.strip() and isinstance(subject, str) and subject.strip():
        query = {
            "mode": "roll",
            "roll_no": roll_no.strip(),
            "subject": subject.strip(),
            "exam_session": str(body.get("exam_session") or "").strip(),
        }
        return query, "roll", query

    raise ledger.VerificationInputError(
        "Provide 'record_id', or 'roll_no' + 'subject', or 'hash', or 'qr_payload'."
    )


# --------------------------------------------------------------------------
# Static frontend (so http://127.0.0.1:5000 just works)
# --------------------------------------------------------------------------

@app.get("/")
def serve_index():
    """Serve the single-page frontend."""
    try:
        return send_from_directory(FRONTEND_DIR, "index.html")
    except OSError:
        return jsonify({
            "ok": False,
            "error": "frontend/index.html was not found. Start the API and open the "
                     "frontend files directly, or restore the frontend folder.",
        }), 404


@app.get("/<path:filename>")
def serve_static(filename: str):
    """Serve styles.css / app.js / etc. from the frontend folder."""
    if filename.startswith("api/"):
        return api_error("Unknown API endpoint.", "NOT_FOUND", 404)
    try:
        return send_from_directory(FRONTEND_DIR, filename)
    except OSError:
        return api_error(f"'{filename}' was not found.", "NOT_FOUND", 404)


# --------------------------------------------------------------------------
# API: health
# --------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """Quick liveness probe — also exposes the genesis hash for teaching."""
    return api_ok({
        "service": "VeriGrade",
        "genesis_hash": ledger.GENESIS_HASH,
        "database": db.DB_PATH,
        "checked_at": ledger.utc_now_iso(),
    })


# --------------------------------------------------------------------------
# API: publish
# --------------------------------------------------------------------------

@app.post("/api/publish")
def publish_result():
    """
    Append a new PUBLISH entry.

    Rejects duplicates of ``(roll_no, subject, exam_session)`` with HTTP 409
    rather than silently overwriting — history is sacred.
    """
    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)

    clean, problems = parse_result_payload(body)
    if problems:
        return api_error(" ".join(problems), "VALIDATION_ERROR", 400, details=problems)

    existing = db.find_publish(clean["roll_no"], clean["subject"], clean["exam_session"])
    if existing:
        return api_error(
            f"Duplicate result: a PUBLISH entry already exists for roll "
            f"'{clean['roll_no']}' in subject '{clean['subject']}' for session "
            f"'{clean['exam_session']}'. Overwriting is never allowed — use the "
            f"2-admin correction flow to change marks.",
            "DUPLICATE_RECORD", 409,
            existing_entry_id=existing["id"],
        )

    entry = {
        "entry_type": "PUBLISH",
        "roll_no": clean["roll_no"],
        "student_name": clean["student_name"],
        "subject": clean["subject"],
        "marks": clean["marks"],
        "exam_session": clean["exam_session"],
        "target_id": None,
        "supersedes_id": None,
        "reason": None,
        "approver_1": None,
        "approver_2": None,
        "created_at": ledger.utc_now_iso(),
    }

    # Seal into the chain: prev_hash = hash of the newest existing entry.
    entry = ledger.prepare_entry(entry, db.fetch_tip_hash())

    try:
        entry_id = db.insert_entry(entry)
    except sqlite3.IntegrityError as exc:
        return api_error(
            f"The ledger rejected this entry ({exc}). Please retry.",
            "INSERT_FAILED", 409,
        )

    entry["id"] = entry_id
    return api_ok({
        "message": f"Result #{entry_id} published and sealed into the hash chain.",
        "record": ledger.describe_entry(entry),
        "qr_payload": ledger.build_qr_payload(entry),
    }, 201)


# --------------------------------------------------------------------------
# API: verify
# --------------------------------------------------------------------------

@app.post("/api/verify")
def verify_result():
    """
    Verify a result by record id, roll+subject, SHA-256 hash or QR payload.

    Always answers with one of VERIFIED / TAMPERED / CORRUPTED / INVALID plus
    the full proof (stored hash, recomputed hash, prev_hash, chain position).
    """
    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400, status="INVALID")

    try:
        query, mode, value = parse_verify_request(body)
    except ledger.VerificationInputError as exc:
        return api_error(str(exc), "INVALID_QUERY", 400, status="INVALID")

    audit = ledger.audit_chain(db.fetch_all_entries())
    result = ledger.run_verification(audit["entries"], audit["summary"], query, mode, value)
    return jsonify(result)


# --------------------------------------------------------------------------
# API: audit + records
# --------------------------------------------------------------------------

@app.route("/api/audit", methods=["GET", "POST"])
def audit_ledger():
    """
    Recompute EVERY record's SHA-256 from its current database values and walk
    the whole chain.

    This is how a direct edit in SQLite is detected: the stored hash was
    computed over the old marks, so it will not match the new ones.
    """
    entries = db.fetch_all_entries()
    audit = ledger.audit_chain(entries)
    return api_ok({
        "checked_at": ledger.utc_now_iso(),
        "chain": audit["summary"],
        "entries": audit["entries"],
        "records": ledger.build_effective_view(audit["entries"]),
    })


@app.get("/api/records")
def list_records():
    """Return only the *current* results (publish + latest correction applied)."""
    audit = ledger.audit_chain(db.fetch_all_entries())
    return api_ok({
        "chain": audit["summary"],
        "records": ledger.build_effective_view(audit["entries"]),
    })


@app.get("/api/records/<int:entry_id>")
def get_record(entry_id: int):
    """Full detail (record + proof + correction history) for one ledger entry."""
    audit = ledger.audit_chain(db.fetch_all_entries())
    target = ledger._find_entry(audit["entries"], entry_id)
    if target is None:
        return api_error(f"No ledger entry with id #{entry_id}.", "NOT_FOUND", 404)
    query = {"mode": "id", "record_id": entry_id}
    return jsonify(ledger.run_verification(
        audit["entries"], audit["summary"], query, "id", entry_id
    ))


# --------------------------------------------------------------------------
# API: corrections (2-admin, append-only)
# --------------------------------------------------------------------------

@app.post("/api/corrections")
def create_correction():
    """
    Append a CORRECTION entry.

    Rules enforced here:
      * the target must be an existing published result
      * new marks must be 0-100 and must actually differ from current marks
      * a non-empty reason is mandatory
      * two DISTINCT approver IDs/names are mandatory
    The original row is never touched — this only appends a new chained entry.
    """
    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)
    if not isinstance(body, dict):
        return api_error("Request body must be a JSON object.", "VALIDATION_ERROR", 400)

    audit = ledger.audit_chain(db.fetch_all_entries())
    items = audit["entries"]
    summary = audit["summary"]

    # --- 1. Locate the target record --------------------------------------
    target = None
    raw_id = body.get("target_id", body.get("record_id"))
    if raw_id is not None:
        try:
            target_entry_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            return api_error("'target_id' must be a whole number.", "VALIDATION_ERROR", 400)
        target = ledger._find_entry(items, target_entry_id)
        if target is None:
            return api_error(
                f"No ledger entry with id #{target_entry_id}.", "NOT_FOUND", 404
            )
    else:
        roll_no, p1 = clean_text(body.get("roll_no"), "roll_no", 40)
        subject, p2 = clean_text(body.get("subject"), "subject", 60)
        session, p3 = clean_text(
            body.get("exam_session"), "exam_session", 30,
            required=False, default=DEFAULT_SESSION,
        )
        problems = [p for p in (p1, p2, p3) if p]
        if problems:
            return api_error(" ".join(problems), "VALIDATION_ERROR", 400, details=problems)

        matches = [
            item for item in items
            if ledger._matches_key(item, roll_no, subject, session or "")
        ]
        if not matches:
            return api_error(
                f"No published result found for roll '{roll_no}' in subject "
                f"'{subject}' ({session}).",
                "NOT_FOUND", 404,
            )
        matches.sort(key=lambda item: int(item.get("id") or 0))
        target = matches[-1]

    # --- 2. Resolve the group so we know the current marks ------------------
    root = target
    if target.get("entry_type") == "CORRECTION":
        root = ledger._find_entry(items, target.get("target_id"))
        if root is None:
            return api_error(
                "This correction points at a record that no longer exists.",
                "BROKEN_TARGET", 409,
            )
    group = ledger.group_for(items, root)
    publisher = group["publish"]
    effective = group["effective"]

    # --- 3. Validate the correction payload -------------------------------
    new_marks, marks_problem = clean_marks(
        body.get("new_marks", body.get("marks")), "new_marks"
    )
    if marks_problem:
        return api_error(marks_problem, "VALIDATION_ERROR", 400)

    reason, reason_problem = clean_text(body.get("reason"), "reason", 300)
    if reason_problem:
        return api_error(reason_problem, "VALIDATION_ERROR", 400)
    if len(reason) < 10:
        return api_error(
            "The correction reason must be at least 10 characters so the audit trail is meaningful.",
            "VALIDATION_ERROR", 400,
        )

    approver_1, a1_problem = clean_text(body.get("approver_1"), "approver_1", 60)
    approver_2, a2_problem = clean_text(body.get("approver_2"), "approver_2", 60)
    problems = [p for p in (a1_problem, a2_problem) if p]
    if problems:
        return api_error(" ".join(problems), "VALIDATION_ERROR", 400, details=problems)
    if approver_1.lower() == approver_2.lower():
        return api_error(
            "Two DIFFERENT approvers are required — approver_1 and approver_2 cannot be the same person.",
            "APPROVERS_NOT_DISTINCT", 400,
        )

    current_marks = ledger._as_int(effective.get("marks"))
    if current_marks == new_marks:
        return api_error(
            f"The corrected marks ({new_marks}) are identical to the current marks — "
            "nothing would change, so no correction entry was appended.",
            "NO_CHANGE", 400,
        )

    # --- 4. Append the correction as a new chained entry --------------------
    entry = {
        "entry_type": "CORRECTION",
        "roll_no": publisher.get("roll_no"),
        "student_name": publisher.get("student_name"),
        "subject": publisher.get("subject"),
        "marks": new_marks,
        "exam_session": publisher.get("exam_session"),
        "target_id": ledger._as_int(publisher.get("id")),
        "supersedes_id": ledger._as_int(effective.get("id")),
        "reason": reason,
        "approver_1": approver_1,
        "approver_2": approver_2,
        "created_at": ledger.utc_now_iso(),
    }
    entry = ledger.prepare_entry(entry, db.fetch_tip_hash())

    try:
        entry_id = db.insert_entry(entry)
    except sqlite3.IntegrityError as exc:
        return api_error(f"The ledger rejected this correction ({exc}).", "INSERT_FAILED", 409)

    entry["id"] = entry_id
    response = {
        "message": (
            f"Correction #{entry_id} appended for record #{publisher['id']} "
            f"({current_marks} → {new_marks}). The original entry is untouched."
        ),
        "record": ledger.describe_entry(entry),
        "qr_payload": ledger.build_qr_payload(entry),
        "original_entry_id": publisher.get("id"),
    }
    if not summary["chain_intact"]:
        response["chain_warning"] = (
            "Heads up: the ledger chain was already broken before this correction, so "
            "this new entry will also be flagged as downstream of that tamper."
        )
    return api_ok(response, 201)


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"VeriGrade API -> http://0.0.0.0:{port}   (database: {db.DB_PATH})")
    app.run(host="0.0.0.0", port=port, debug=False)