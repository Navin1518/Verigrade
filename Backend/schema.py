from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    text,
)


metadata = MetaData()

ledger = Table(
    "ledger",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entry_type", Text, nullable=False),
    Column("roll_no", Text),
    Column("student_name", Text),
    Column("subject", Text),
    Column("marks", Integer),
    Column("max_marks", Integer),
    Column("exam_session", Text),
    Column("target_id", Integer),
    Column("supersedes_id", Integer),
    Column("reason", Text),
    Column("approver_1", Text),
    Column("approver_2", Text),
    Column("prev_hash", Text),
    Column("record_hash", Text),
    Column("created_at", Text),
)

Index("idx_ledger_key", ledger.c.roll_no, ledger.c.subject, ledger.c.exam_session)
Index("idx_ledger_record_hash", ledger.c.record_hash, unique=True)
Index(
    "idx_ledger_publish_unique",
    func.lower(func.trim(ledger.c.roll_no)),
    func.lower(func.trim(ledger.c.subject)),
    func.lower(func.trim(ledger.c.exam_session)),
    unique=True,
    sqlite_where=ledger.c.entry_type == "PUBLISH",
    postgresql_where=ledger.c.entry_type == "PUBLISH",
)

ledger_metadata = Table(
    "ledger_metadata",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("email", Text, nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("role", Text, nullable=False, server_default=text("'viewer'")),
    Column("created_at", Text, nullable=False),
)

correction_requests = Table(
    "correction_requests",
    metadata,
    Column("id", String, primary_key=True),
    Column("target_id", Integer, ForeignKey("ledger.id"), nullable=False),
    Column("original_marks", Integer, nullable=False),
    Column("requested_marks", Integer, nullable=False),
    Column("reason", Text, nullable=False),
    Column("requester_user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("status", Text, nullable=False, server_default=text("'Pending'")),
    Column("approver_1_user_id", Integer, ForeignKey("users.id")),
    Column("approver_2_user_id", Integer, ForeignKey("users.id")),
    Column("rejected_by_user_id", Integer, ForeignKey("users.id")),
    Column("correction_entry_id", Integer, ForeignKey("ledger.id")),
    Column("rejection_reason", Text),
    Column("created_at", Text, nullable=False),
    Column("decided_at", Text),
    CheckConstraint(
        "status IN ('Pending', 'Approved', 'Rejected')",
        name="ck_correction_requests_status",
    ),
)

Index(
    "idx_correction_requests_status_created",
    correction_requests.c.status,
    correction_requests.c.created_at.desc(),
)
Index(
    "idx_correction_requests_pending_target",
    correction_requests.c.target_id,
    unique=True,
    sqlite_where=correction_requests.c.status == "Pending",
    postgresql_where=correction_requests.c.status == "Pending",
)
