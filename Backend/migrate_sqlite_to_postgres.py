import argparse
import os
import sqlite3
import sys

from sqlalchemy import create_engine, func, select, text

from Backend.db_config import normalize_database_url
from Backend.ledger import audit_chain
from Backend.schema import correction_requests, ledger, metadata, users


TABLES = (
    metadata.tables["ledger"],
    metadata.tables["ledger_metadata"],
    users,
    correction_requests,
)


def migrate(source_path: str, target_url: str) -> dict:
    normalized_url = normalize_database_url(target_url)
    if not normalized_url.startswith("postgresql+psycopg://"):
        raise ValueError("The target database URL must use PostgreSQL.")
    if not os.path.isfile(source_path):
        raise FileNotFoundError("The SQLite source database file was not found.")

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    source.execute("BEGIN")
    target_engine = create_engine(
        normalized_url, pool_pre_ping=True, hide_parameters=True
    )
    try:
        source_entries = [
            dict(row)
            for row in source.execute("SELECT * FROM ledger ORDER BY id ASC").fetchall()
        ]
        if not audit_chain(source_entries)["summary"]["chain_intact"]:
            raise ValueError("The source ledger failed its integrity check; migration stopped.")

        with target_engine.begin() as target:
            versions = target.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
            if not versions:
                raise RuntimeError("Run `python -m Backend.migrate_db` on the target first.")
            if any(
                target.execute(select(func.count()).select_from(table)).scalar_one()
                for table in TABLES
            ):
                raise RuntimeError(
                    "The target database is not empty. Refusing to overwrite existing data."
                )

            expected_counts = {}
            for table in TABLES:
                rows = [
                    dict(row)
                    for row in source.execute(
                        f'SELECT * FROM "{table.name}" ORDER BY 1'
                    ).fetchall()
                ]
                expected_counts[table.name] = len(rows)
                if rows:
                    target.execute(table.insert(), rows)

            for table_name in ("ledger", "users"):
                target.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                    f"COALESCE(MAX(id), 1), COUNT(*) > 0) FROM {table_name}"
                ))

            actual_counts = {
                table.name: target.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()
                for table in TABLES
            }
            if actual_counts != expected_counts:
                raise RuntimeError("Post-migration row counts did not match the source.")
            migrated_entries = [
                dict(row)
                for row in target.execute(
                    select(ledger).order_by(ledger.c.id)
                ).mappings().all()
            ]
            if not audit_chain(migrated_entries)["summary"]["chain_intact"]:
                raise RuntimeError("The migrated ledger failed its integrity check.")
        return actual_counts
    finally:
        source.close()
        target_engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="Copy a verified SQLite VeriGrade database into an empty PostgreSQL database."
    )
    parser.add_argument("--source", required=True, help="Path to a SQLite backup file")
    args = parser.parse_args()
    target_url = os.environ.get("VERIGRADE_MIGRATION_DATABASE_URL")
    if not target_url:
        parser.error("Set VERIGRADE_MIGRATION_DATABASE_URL without printing or sharing it.")
    counts = migrate(args.source, target_url)
    print("Migration verified. Copied row counts: " + ", ".join(
        f"{name}={count}" for name, count in counts.items()
    ))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Migration failed: {error}", file=sys.stderr)
        raise
