import os
import time
from urllib.parse import quote_plus

from sqlalchemy.exc import OperationalError

from sqlalchemy import create_engine, event, inspect, text, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker


def _load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        print(f"Failed to load {path}: {exc}")


_load_env_file()


def _build_database_url() -> str:
    explicit_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    if explicit_url:
        return explicit_url
    user = os.getenv("POSTGRES_USER", "postgres")
    password = quote_plus(os.getenv("POSTGRES_PASSWORD", ""))
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "gscript")
    base_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"
    sslmode = os.getenv("POSTGRES_SSLMODE")
    if sslmode:
        return f"{base_url}?sslmode={sslmode}"
    return base_url


DATABASE_URL = _build_database_url()

engine_kwargs = {"future": True, "pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = scoped_session(
    sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
)

Base = declarative_base()


def init_db() -> None:
    os.makedirs("instance", exist_ok=True)
    import backend.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_user_admin_column()
    _ensure_user_trusted_until_column()
    _ensure_user_columns()
    _ensure_commercials_state_sequence()
    _assign_existing_rows()


def _ensure_user_columns() -> None:
    tables = {
        "contracts": "user_id INTEGER",
        "warehouse_items": "user_id INTEGER",
        "warehouse_incomes": "user_id INTEGER",
        "warehouse_expenses": "user_id INTEGER",
        "price_items": "user_id INTEGER",
        "commercials_state": "user_id INTEGER",
        "archive_entries": "user_id INTEGER",
    }
    with engine.begin() as conn:
        for table, column_def in tables.items():
            inspector = inspect(engine)
            if not inspector.has_table(table):
                continue
            columns = {col["name"] for col in inspector.get_columns(table)}
            if "user_id" in columns:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_def}"))


def _ensure_user_admin_column() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return
    columns = {col["name"] for col in inspector.get_columns("users")}
    if "is_admin" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE"))


def _ensure_user_trusted_until_column() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return
    columns = {col["name"] for col in inspector.get_columns("users")}
    if "email_otp_trusted_until" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN email_otp_trusted_until TIMESTAMP"))


def _assign_existing_rows() -> None:
    from backend.models import User

    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return
    session = SessionLocal()
    try:
        template_user_id = session.execute(select(User.id).order_by(User.id)).scalar()
        if not template_user_id:
            return
        tables = [
            "contracts",
            "warehouse_items",
            "warehouse_incomes",
            "warehouse_expenses",
            "price_items",
            "commercials_state",
            "archive_entries",
        ]
        with engine.begin() as conn:
            for table in tables:
                conn.execute(
                    text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),
                    {"uid": template_user_id},
                )
    finally:
        session.close()


def _ensure_commercials_state_sequence() -> None:
    if not DATABASE_URL.startswith("postgresql"):
        return
    inspector = inspect(engine)
    if not inspector.has_table("commercials_state"):
        return
    with engine.begin() as conn:
        conn.execute(text("CREATE SEQUENCE IF NOT EXISTS commercials_state_id_seq"))
        conn.execute(
            text(
                "SELECT setval('commercials_state_id_seq', GREATEST(COALESCE((SELECT MAX(id) FROM commercials_state), 1), 1), true)"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE commercials_state ALTER COLUMN id SET DEFAULT nextval('commercials_state_id_seq')"
            )
        )
        conn.execute(text("ALTER SEQUENCE commercials_state_id_seq OWNED BY commercials_state.id"))


def commit_with_retry(session, retries: int = 5, base_delay: float = 0.05) -> None:
    attempt = 0
    while True:
        try:
            session.commit()
            return
        except OperationalError as exc:
            message = str(exc).lower()
            if "database is locked" not in message or attempt >= retries:
                raise
            session.rollback()
            time.sleep(base_delay * (2**attempt))
            attempt += 1


@event.listens_for(Engine, "connect")
def configure_sqlite(dbapi_connection, connection_record) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
