"""Create the VeriGrade database schema.

Revision ID: 0001_initial_schema
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing_tables = set(inspect(bind).get_table_names())
    if "ledger" not in existing_tables:
        op.create_table(
            "ledger",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("entry_type", sa.Text(), nullable=False),
            sa.Column("roll_no", sa.Text()),
            sa.Column("student_name", sa.Text()),
            sa.Column("subject", sa.Text()),
            sa.Column("marks", sa.Integer()),
            sa.Column("max_marks", sa.Integer()),
            sa.Column("exam_session", sa.Text()),
            sa.Column("target_id", sa.Integer()),
            sa.Column("supersedes_id", sa.Integer()),
            sa.Column("reason", sa.Text()),
            sa.Column("approver_1", sa.Text()),
            sa.Column("approver_2", sa.Text()),
            sa.Column("prev_hash", sa.Text()),
            sa.Column("record_hash", sa.Text()),
            sa.Column("created_at", sa.Text()),
        )
    else:
        columns = {column["name"] for column in inspect(bind).get_columns("ledger")}
        if "max_marks" not in columns:
            op.add_column("ledger", sa.Column("max_marks", sa.Integer))
    if "ledger_metadata" not in existing_tables:
        op.create_table(
            "ledger_metadata",
            sa.Column("key", sa.String(), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False),
        )
    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("email", sa.Text(), nullable=False, unique=True),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("role", sa.Text(), nullable=False, server_default="viewer"),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
    if "correction_requests" not in existing_tables:
        op.create_table(
            "correction_requests",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("target_id", sa.Integer(), sa.ForeignKey("ledger.id"), nullable=False),
            sa.Column("original_marks", sa.Integer(), nullable=False),
            sa.Column("requested_marks", sa.Integer(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("requester_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default="Pending"),
            sa.Column("approver_1_user_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("approver_2_user_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("rejected_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("correction_entry_id", sa.Integer(), sa.ForeignKey("ledger.id")),
            sa.Column("rejection_reason", sa.Text()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("decided_at", sa.Text()),
            sa.CheckConstraint(
                "status IN ('Pending', 'Approved', 'Rejected')",
                name="ck_correction_requests_status",
            ),
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_key "
        "ON ledger (roll_no, subject, exam_session)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_record_hash ON ledger (record_hash)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_publish_unique "
        "ON ledger (lower(trim(roll_no)), lower(trim(subject)), lower(trim(exam_session))) "
        "WHERE entry_type = 'PUBLISH'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_correction_requests_status_created "
        "ON correction_requests (status, created_at DESC)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_correction_requests_pending_target "
        "ON correction_requests (target_id) WHERE status = 'Pending'"
    )


def downgrade():
    raise RuntimeError(
        "The initial schema migration is not reversible; restore from a verified backup."
    )
