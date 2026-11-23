import os


if __name__ == "__main__":
    migration_url = os.environ.get("VERIGRADE_MIGRATION_DATABASE_URL")
    if migration_url and not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = migration_url
    from . import database

    database.init_db()
