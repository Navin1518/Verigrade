import os

from sqlalchemy.engine import URL, make_url


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def sqlite_path() -> str:
    if os.environ.get("VERIGRADE_DB"):
        return os.environ["VERIGRADE_DB"]
    volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        return os.path.join(volume, "results.db")
    return os.path.join(BASE_DIR, "results.db")


def database_url() -> str:
    configured = os.environ.get("DATABASE_URL")
    if configured:
        return normalize_database_url(configured)
    return URL.create("sqlite", database=sqlite_path()).render_as_string(
        hide_password=False
    )


def normalize_database_url(configured: str) -> str:
    url = make_url(configured)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def is_sqlite_url(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"
