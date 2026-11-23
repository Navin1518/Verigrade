from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, event, pool, text

from Backend.db_config import database_url, is_sqlite_url
from Backend.schema import metadata


config = context.config
config.set_main_option("sqlalchemy.url", database_url().replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def run_migrations_offline():
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    url = database_url()
    options = {"poolclass": pool.NullPool, "hide_parameters": True}
    if is_sqlite_url(url):
        options["connect_args"] = {"timeout": 15}
    connectable = create_engine(url, **options)

    if is_sqlite_url(url):
        @event.listens_for(connectable, "connect")
        def enable_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            if not is_sqlite_url(url):
                connection.execute(text("SELECT pg_advisory_xact_lock(741009813)"))
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
