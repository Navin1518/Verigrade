import unittest

from Backend.db_config import normalize_database_url
from Backend.migrate_sqlite_to_postgres import TABLES, migrate


class DatabaseMigrationTests(unittest.TestCase):
    def test_postgresql_urls_use_the_psycopg_driver(self):
        self.assertEqual(
            normalize_database_url("postgresql://localhost/verigrade"),
            "postgresql+psycopg://localhost/verigrade",
        )
        self.assertEqual(
            [table.name for table in TABLES],
            ["ledger", "ledger_metadata", "users", "correction_requests"],
        )
        self.assertEqual(
            normalize_database_url("postgres://localhost/verigrade"),
            "postgresql+psycopg://localhost/verigrade",
        )

    def test_migration_refuses_non_postgresql_targets(self):
        with self.assertRaisesRegex(ValueError, "target database URL must use PostgreSQL"):
            migrate("not-opened.db", "sqlite:///target.db")


if __name__ == "__main__":
    unittest.main()
