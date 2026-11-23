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
import hmac
import os
import re
import secrets

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

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
session_secret = os.environ.get("VERIGRADE_SESSION_SECRET")
if not session_secret:
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RENDER"):
        raise RuntimeError("VERIGRADE_SESSION_SECRET must be configured in production.")
    session_secret = "local-development-only-change-before-deploy"
secure_session_cookie = os.environ.get("VERIGRADE_COOKIE_SECURE", "1") == "1"
app.config["SECRET_KEY"] = session_secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="None" if secure_session_cookie else "Lax",
    SESSION_COOKIE_SECURE=secure_session_cookie,
)
allowed_origins = [
    origin.strip()
    for origin in os.environ.get("VERIGRADE_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
CORS(
    app,
    resources={r"/api/*": {"origins": allowed_origins or "null"}},
    allow_headers=["Content-Type", "Authorization", "X-Verigrade-Approver-2", "X-CSRF-Token"],
    supports_credentials=True,
)


DEFAULT_SESSION = "2025-2026"
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def load_api_principals() -> dict[str, dict]:
    """Load API-key principals from VERIGRADE_API_KEYS JSON."""
    raw = os.environ.get("VERIGRADE_API_KEYS", "").strip()
    if not raw:
        return {}
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(configured, dict):
        return {}

    principals = {}
    for api_key, value in configured.items():
        if not isinstance(api_key, str) or not api_key:
            continue
        if isinstance(value, str):
            value = {"name": value, "roles": ["publisher", "approver"]}
        if not isinstance(value, dict):
            continue
        name = value.get("name")
        roles = value.get("roles")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(roles, list):
            continue
        principals[api_key] = {
            "name": name.strip(),
            "roles": {role for role in roles if role in ("publisher", "approver")},
        }
    return principals


def authenticate_principal(required_role: str, header_name: str = "Authorization"):
    """Authenticate one Bearer/X-API-Key principal and return it or an error."""
    raw = request.headers.get(header_name, "")
    if header_name == "Authorization" and raw.lower().startswith("bearer "):
        api_key = raw[7:].strip()
    else:
        api_key = raw.strip()

    if not api_key:
        return None, api_error(
            "Authentication is required.", "UNAUTHENTICATED", 401
        )

    for configured_key, principal in load_api_principals().items():
        if hmac.compare_digest(api_key, configured_key):
            if required_role not in principal["roles"]:
                return None, api_error(
                    "This credential is not authorized for this operation.",
                    "FORBIDDEN",
                    403,
                )
            return principal, None

    return None, api_error("Invalid API credential.", "UNAUTHENTICATED", 401)


def authenticated_session_user(required_role: str | None = None):
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = db.find_user_by_id(user_id)
    if not user:
        session.clear()
        return None
    user = promote_configured_admin(user)
    if required_role:
        role = user["role"]
        permitted = role == "admin" or role == required_role
        if not permitted:
            return None
    return user


def promote_configured_admin(user: dict) -> dict:
    admin_email = os.environ.get("VERIGRADE_ADMIN_EMAIL", "").strip().lower()
    if admin_email and user["email"].lower() == admin_email and user["role"] != "admin":
        return db.update_user_role(user["id"], "admin") or user
    return user


def issue_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def require_csrf():
    expected = session.get("csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not hmac.compare_digest(expected, supplied):
        return api_error("A valid CSRF token is required.", "CSRF_FAILED", 403)
    return None


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

    max_marks = payload.get("max_marks", payload.get("max"))
    if max_marks is None:
        max_marks = 100
    max_marks, max_problem = clean_marks(max_marks, "max_marks")
    if max_problem:
        problems.append(max_problem)
    elif max_marks <= 0:
        problems.append("'max_marks' must be greater than 0.")
    else:
        clean["max_marks"] = max_marks
        if clean.get("marks") is not None and clean["marks"] > max_marks:
            problems.append("'marks' cannot be greater than 'max_marks'.")

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
# API: login and signup
# --------------------------------------------------------------------------

@app.post("/api/auth/signup")
def signup():
    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)
    if not isinstance(body, dict):
        return api_error("Request body must be a JSON object.", "VALIDATION_ERROR", 400)

    name, name_problem = clean_text(body.get("name"), "name", 80)
    email, email_problem = clean_text(body.get("email"), "email", 120)
    password = body.get("password")
    if name_problem:
        return api_error(name_problem, "VALIDATION_ERROR", 400)
    if email_problem:
        return api_error(email_problem, "VALIDATION_ERROR", 400)
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return api_error("Enter a valid email address.", "VALIDATION_ERROR", 400)
    if not isinstance(password, str) or len(password) < 8:
        return api_error(
            "Password must be at least 8 characters.",
            "VALIDATION_ERROR",
            400,
        )
    if db.find_user_by_email(email):
        return api_error(
            "An account with this email already exists.",
            "EMAIL_EXISTS",
            409,
        )

    try:
        user = db.create_user(
            name,
            email,
            generate_password_hash(password, method="scrypt"),
        )
    except db.INTEGRITY_ERRORS:
        return api_error("An account with this email already exists.", "EMAIL_EXISTS", 409)

    user = promote_configured_admin(user)
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    csrf_token = issue_csrf_token()
    return api_ok({"message": "Account created.", "user": user, "csrf_token": csrf_token}, 201)


@app.post("/api/auth/login")
def login():
    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)
    if not isinstance(body, dict):
        return api_error("Request body must be a JSON object.", "VALIDATION_ERROR", 400)

    email, problem = clean_text(body.get("email"), "email", 120)
    password = body.get("password")
    if problem:
        return api_error(problem, "VALIDATION_ERROR", 400)
    if not isinstance(password, str):
        return api_error("Password is required.", "VALIDATION_ERROR", 400)

    user = db.find_user_by_email(email)
    if not user or not check_password_hash(user["password_hash"], password):
        return api_error("Invalid email or password.", "UNAUTHENTICATED", 401)

    user = promote_configured_admin(user)
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    csrf_token = issue_csrf_token()
    return api_ok({
        "message": "Signed in.",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
        },
        "csrf_token": csrf_token,
    })


@app.post("/api/auth/logout")
def logout():
    if session.get("user_id"):
        csrf_error = require_csrf()
        if csrf_error:
            return csrf_error
    session.clear()
    return api_ok({"message": "Signed out."})


@app.get("/api/auth/me")
def auth_me():
    user = db.find_user_by_id(session.get("user_id")) if session.get("user_id") else None
    if not user:
        return api_error("Not signed in.", "UNAUTHENTICATED", 401)
    user = promote_configured_admin(user)
    return api_ok({
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
        },
        "csrf_token": issue_csrf_token(),
    })


@app.get("/api/admin/users")
def admin_list_users():
    user = authenticated_session_user()
    if not user:
        return api_error("Sign-in is required.", "UNAUTHENTICATED", 401)
    if user["role"] != "admin":
        return api_error("Administrator access is required.", "FORBIDDEN", 403)
    return api_ok({"users": db.list_users()})


@app.patch("/api/admin/users/<int:user_id>/role")
def admin_update_user_role(user_id: int):
    user = authenticated_session_user()
    if not user:
        return api_error("Sign-in is required.", "UNAUTHENTICATED", 401)
    if user["role"] != "admin":
        return api_error("Administrator access is required.", "FORBIDDEN", 403)
    csrf_error = require_csrf()
    if csrf_error:
        return csrf_error

    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)
    if not isinstance(body, dict):
        return api_error("Request body must be a JSON object.", "VALIDATION_ERROR", 400)
    role = body.get("role")
    if not isinstance(role, str) or role not in db.USER_ROLES:
        return api_error("Choose a valid user role.", "VALIDATION_ERROR", 400)

    target = db.find_user_by_id(user_id)
    if not target:
        return api_error("User not found.", "NOT_FOUND", 404)
    admin_email = os.environ.get("VERIGRADE_ADMIN_EMAIL", "").strip().lower()
    if target["email"].lower() == admin_email and role != "admin":
        return api_error("The configured bootstrap administrator cannot be demoted.", "FORBIDDEN", 403)
    if target["id"] == user["id"] and role != "admin":
        return api_error("You cannot change your own administrator role.", "FORBIDDEN", 403)
    try:
        updated = db.update_user_role(user_id, role)
    except ValueError as exc:
        return api_error(str(exc), "FORBIDDEN", 409)
    if not updated:
        return api_error("User not found.", "NOT_FOUND", 404)
    return api_ok({"user": updated})


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
    user = authenticated_session_user()
    if user:
        if user["role"] not in ("publisher", "admin"):
            return api_error("A publisher role is required.", "FORBIDDEN", 403)
        csrf_error = require_csrf()
        if csrf_error:
            return csrf_error
        principal = {"name": user["name"], "roles": {"publisher"}}
    else:
        principal, auth_error = authenticate_principal("publisher")
        if auth_error:
            return auth_error

    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)

    clean, problems = parse_result_payload(body)
    if problems:
        return api_error(" ".join(problems), "VALIDATION_ERROR", 400, details=problems)

    entry = {
        "entry_type": "PUBLISH",
        "roll_no": clean["roll_no"],
        "student_name": clean["student_name"],
        "subject": clean["subject"],
        "marks": clean["marks"],
        "max_marks": clean["max_marks"],
        "exam_session": clean["exam_session"],
        "target_id": None,
        "supersedes_id": None,
        "reason": None,
        "approver_1": None,
        "approver_2": None,
        "created_at": ledger.utc_now_iso(),
    }

    try:
        entry_id = db.append_entry(
            entry,
            (clean["roll_no"], clean["subject"], clean["exam_session"]),
        )
    except db.INTEGRITY_ERRORS as exc:
        if "DUPLICATE_PUBLISH" in str(exc):
            existing = db.find_publish(
                clean["roll_no"], clean["subject"], clean["exam_session"]
            )
            return api_error(
                f"Duplicate result: a PUBLISH entry already exists for roll "
                f"'{clean['roll_no']}' in subject '{clean['subject']}' for session "
                f"'{clean['exam_session']}'. Overwriting is never allowed — use the "
                f"2-admin correction flow to change marks.",
                "DUPLICATE_RECORD", 409,
                existing_entry_id=existing["id"] if existing else None,
            )
        return api_error(
            f"The ledger rejected this entry ({exc}). Please retry.",
            "INSERT_FAILED", 409,
        )

    entry = db.fetch_entry(entry_id)
    if entry is None:
        return api_error("The published entry could not be loaded.", "INSERT_FAILED", 500)
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


def describe_correction_request(item: dict) -> dict:
    rejected = item["status"] == "Rejected"
    return {
        "id": item["id"],
        "recordId": str(item["target_id"]),
        "rollNo": item["roll_no"],
        "subject": item["subject"],
        "original": item["original_marks"],
        "requested": item["requested_marks"],
        "reason": item["reason"],
        "status": item["status"],
        "first": "Rejected" if rejected else ("Approved" if item["approver_1_user_id"] else "Pending"),
        "second": "Approved" if item["approver_2_user_id"] else "Pending",
        "firstName": item["rejected_by_name"] if rejected else item["approver_1_name"],
        "secondName": item["approver_2_name"],
        "requestedBy": item["requested_by_name"],
        "requestedAt": item["created_at"],
        "date": item["decided_at"] or "",
        "rejectionReason": item["rejection_reason"] or "",
    }


@app.get("/api/correction-requests")
def list_correction_requests():
    if not authenticated_session_user():
        return api_error("Sign-in is required.", "UNAUTHENTICATED", 401)
    return api_ok({
        "requests": [
            describe_correction_request(item)
            for item in db.list_correction_requests()
        ]
    })


@app.post("/api/correction-requests")
def submit_correction_request():
    user = authenticated_session_user()
    if not user:
        return api_error("Sign-in is required.", "UNAUTHENTICATED", 401)
    csrf_error = require_csrf()
    if csrf_error:
        return csrf_error

    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)
    if not isinstance(body, dict):
        return api_error("Request body must be a JSON object.", "VALIDATION_ERROR", 400)
    try:
        target_id = int(str(body.get("target_id", "")).strip())
    except (TypeError, ValueError):
        return api_error("'target_id' must be a whole number.", "VALIDATION_ERROR", 400)
    if target_id < 1:
        return api_error("'target_id' must be 1 or greater.", "VALIDATION_ERROR", 400)

    requested_marks, marks_problem = clean_marks(
        body.get("requested_marks"), "requested_marks"
    )
    if marks_problem:
        return api_error(marks_problem, "VALIDATION_ERROR", 400)
    reason, reason_problem = clean_text(body.get("reason"), "reason", 300)
    if reason_problem:
        return api_error(reason_problem, "VALIDATION_ERROR", 400)
    if len(reason) < 10:
        return api_error(
            "The correction reason must be at least 10 characters.",
            "VALIDATION_ERROR",
            400,
        )

    items = ledger.audit_chain(db.fetch_all_entries())["entries"]
    target = ledger._find_entry(items, target_id)
    if target is None:
        return api_error("The result to correct was not found.", "NOT_FOUND", 404)
    root = target
    if target.get("entry_type") == "CORRECTION":
        root = ledger._find_entry(items, target.get("target_id"))
        if root is None:
            return api_error("The target correction has no original result.", "BROKEN_TARGET", 409)
    group = ledger.group_for(items, root)
    effective = group["effective"]
    if ledger._as_int(effective.get("id")) != target_id:
        return api_error(
            "This result has changed. Refresh the ledger before requesting a correction.",
            "STALE_RECORD",
            409,
        )
    original_marks, marks_problem = clean_marks(effective.get("marks"), "current_marks")
    if marks_problem:
        return api_error("The current result is not valid for correction.", "CORRUPTED_RECORD", 409)
    if original_marks == requested_marks:
        return api_error("Requested marks must differ from the current marks.", "NO_CHANGE", 400)

    request_id = "COR-" + secrets.token_hex(12)
    try:
        request_item = db.create_correction_request(
            request_id=request_id,
            target_id=target_id,
            original_marks=original_marks,
            requested_marks=requested_marks,
            reason=reason,
            requester_user_id=user["id"],
            created_at=ledger.utc_now_iso(),
        )
    except db.INTEGRITY_ERRORS as exc:
        if "correction_requests.target_id" in str(exc):
            return api_error(
                "A pending correction request already exists for this result.",
                "REQUEST_PENDING",
                409,
            )
        return api_error("The correction request could not be saved.", "INSERT_FAILED", 409)
    return api_ok({"request": describe_correction_request(request_item)}, 201)


@app.post("/api/correction-requests/<request_id>/approve")
def approve_correction_request(request_id: str):
    user = authenticated_session_user()
    if not user:
        return api_error("Sign-in is required.", "UNAUTHENTICATED", 401)
    if user["role"] not in ("approver", "admin"):
        return api_error("An approver role is required.", "FORBIDDEN", 403)
    csrf_error = require_csrf()
    if csrf_error:
        return csrf_error

    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)
    if not isinstance(body, dict):
        return api_error("Request body must be a JSON object.", "VALIDATION_ERROR", 400)
    second_email, email_problem = clean_text(
        body.get("second_approver_email"), "second_approver_email", 120
    )
    second_password = body.get("second_approver_password")
    if email_problem or not isinstance(second_password, str) or not second_password:
        return api_error(
            "A second approver's email and password are required.",
            "VALIDATION_ERROR",
            400,
        )
    second_user = db.find_user_by_email(second_email)
    if not second_user or not check_password_hash(second_user["password_hash"], second_password):
        return api_error("Invalid second approver credentials.", "UNAUTHENTICATED", 401)
    second_user = promote_configured_admin(second_user)
    if second_user["role"] not in ("approver", "admin"):
        return api_error("The second account does not have an approver role.", "FORBIDDEN", 403)
    if second_user["id"] == user["id"]:
        return api_error(
            "Two different approver accounts are required.",
            "APPROVERS_NOT_DISTINCT",
            400,
        )

    request_item = db.fetch_correction_request(request_id)
    if not request_item:
        return api_error("Correction request not found.", "NOT_FOUND", 404)
    if request_item["status"] != "Pending":
        return api_error("This correction request is no longer pending.", "REQUEST_NOT_PENDING", 409)

    audit = ledger.audit_chain(db.fetch_all_entries())
    items = audit["entries"]
    target = ledger._find_entry(items, request_item["target_id"])
    if target is None:
        return api_error("The result to correct no longer exists.", "NOT_FOUND", 404)
    root = target
    if target.get("entry_type") == "CORRECTION":
        root = ledger._find_entry(items, target.get("target_id"))
        if root is None:
            return api_error("The target correction has no original result.", "BROKEN_TARGET", 409)
    group = ledger.group_for(items, root)
    publisher = group["publish"]
    effective = group["effective"]
    if ledger._as_int(effective.get("id")) != request_item["target_id"]:
        return api_error(
            "This request is stale because the result has already changed.",
            "STALE_REQUEST",
            409,
        )
    current_marks, marks_problem = clean_marks(effective.get("marks"), "current_marks")
    if marks_problem or current_marks != request_item["original_marks"]:
        return api_error("The current marks no longer match this request.", "STALE_REQUEST", 409)

    entry = {
        "entry_type": "CORRECTION",
        "roll_no": publisher.get("roll_no"),
        "student_name": publisher.get("student_name"),
        "subject": publisher.get("subject"),
        "marks": request_item["requested_marks"],
        "max_marks": ledger._as_int(publisher.get("max_marks")) or 100,
        "exam_session": publisher.get("exam_session"),
        "target_id": ledger._as_int(publisher.get("id")),
        "supersedes_id": ledger._as_int(effective.get("id")),
        "reason": request_item["reason"],
        "approver_1": user["name"],
        "approver_2": second_user["name"],
        "created_at": ledger.utc_now_iso(),
    }
    try:
        entry_id = db.append_entry(
            entry,
            correction_request_id=request_id,
            approver_user_ids=(user["id"], second_user["id"]),
        )
    except db.INTEGRITY_ERRORS as exc:
        message = str(exc)
        if "CORRECTION_REQUEST_NOT_PENDING" in message:
            return api_error("This correction request is no longer pending.", "REQUEST_NOT_PENDING", 409)
        if "STALE_CORRECTION_REQUEST" in message:
            return api_error(
                "This request is stale because the result has already changed.",
                "STALE_REQUEST",
                409,
            )
        if "APPROVERS_NOT_DISTINCT" in message:
            return api_error("Two different approver accounts are required.", "APPROVERS_NOT_DISTINCT", 400)
        return api_error("The correction could not be appended to the ledger.", "INSERT_FAILED", 409)

    entry["id"] = entry_id
    updated_request = db.fetch_correction_request(request_id)
    response = {
        "message": f"Correction #{entry_id} appended for request {request_id}.",
        "record": ledger.describe_entry(entry),
        "request": describe_correction_request(updated_request),
        "qr_payload": ledger.build_qr_payload(entry),
    }
    if not audit["summary"]["chain_intact"]:
        response["chain_warning"] = "The ledger was already inconsistent before this correction."
    return api_ok(response, 201)


@app.post("/api/correction-requests/<request_id>/reject")
def reject_correction_request(request_id: str):
    user = authenticated_session_user()
    if not user:
        return api_error("Sign-in is required.", "UNAUTHENTICATED", 401)
    if user["role"] not in ("approver", "admin"):
        return api_error("An approver role is required.", "FORBIDDEN", 403)
    csrf_error = require_csrf()
    if csrf_error:
        return csrf_error

    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)
    if not isinstance(body, dict):
        return api_error("Request body must be a JSON object.", "VALIDATION_ERROR", 400)
    reason, reason_problem = clean_text(body.get("reason"), "reason", 300)
    if reason_problem:
        return api_error(reason_problem, "VALIDATION_ERROR", 400)
    if len(reason) < 10:
        return api_error(
            "The rejection reason must be at least 10 characters.",
            "VALIDATION_ERROR",
            400,
        )
    request_item = db.reject_correction_request(
        request_id,
        user["id"],
        reason,
        ledger.utc_now_iso(),
    )
    if not request_item:
        existing = db.fetch_correction_request(request_id)
        if not existing:
            return api_error("Correction request not found.", "NOT_FOUND", 404)
        return api_error("This correction request is no longer pending.", "REQUEST_NOT_PENDING", 409)
    return api_ok({"request": describe_correction_request(request_item)})


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
    user = authenticated_session_user()
    if user:
        if user["role"] not in ("approver", "admin"):
            return api_error("An approver role is required.", "FORBIDDEN", 403)
        csrf_error = require_csrf()
        if csrf_error:
            return csrf_error
        approver_1 = {"name": user["name"], "user_id": user["id"]}
        approver_2 = None
    else:
        approver_1, auth_error = authenticate_principal("approver", "Authorization")
        if auth_error:
            return auth_error
        approver_2, auth_error = authenticate_principal("approver", "X-Verigrade-Approver-2")
        if auth_error:
            return auth_error
        if approver_1["name"].lower() == approver_2["name"].lower():
            return api_error(
                "Two DIFFERENT authenticated approvers are required.",
                "APPROVERS_NOT_DISTINCT",
                400,
            )

    body, body_error = read_json_body()
    if body_error:
        return api_error(body_error, "MALFORMED_JSON", 400)
    if not isinstance(body, dict):
        return api_error("Request body must be a JSON object.", "VALIDATION_ERROR", 400)
    if user:
        second_email, email_problem = clean_text(
            body.get("second_approver_email"), "second_approver_email", 120
        )
        second_password = body.get("second_approver_password")
        if email_problem or not isinstance(second_password, str) or not second_password:
            return api_error(
                "A second approver's email and password are required.",
                "VALIDATION_ERROR",
                400,
            )
        second_user = db.find_user_by_email(second_email)
        if not second_user or not check_password_hash(second_user["password_hash"], second_password):
            return api_error("Invalid second approver credentials.", "UNAUTHENTICATED", 401)
        second_user = promote_configured_admin(second_user)
        if second_user["role"] not in ("approver", "admin"):
            return api_error("The second account does not have an approver role.", "FORBIDDEN", 403)
        if second_user["id"] == user["id"]:
            return api_error(
                "Two different approver accounts are required.",
                "APPROVERS_NOT_DISTINCT",
                400,
            )
        approver_2 = {"name": second_user["name"], "user_id": second_user["id"]}

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
        "max_marks": ledger._as_int(publisher.get("max_marks")) or 100,
        "exam_session": publisher.get("exam_session"),
        "target_id": ledger._as_int(publisher.get("id")),
        "supersedes_id": ledger._as_int(effective.get("id")),
        "reason": reason,
        "approver_1": approver_1["name"],
        "approver_2": approver_2["name"],
        "created_at": ledger.utc_now_iso(),
    }
    try:
        entry_id = db.append_entry(entry)
    except db.INTEGRITY_ERRORS as exc:
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