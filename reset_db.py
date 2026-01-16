from backend.db import Base, DATABASE_URL, engine, init_db
import backend.models  # noqa: F401
from sqlalchemy import text


def main() -> None:
    if DATABASE_URL.startswith("postgresql"):
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    else:
        Base.metadata.drop_all(bind=engine)
    init_db()


if __name__ == "__main__":
    main()
