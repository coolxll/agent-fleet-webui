from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_engine = None
_SessionLocal = None


class Base(DeclarativeBase):
    pass


def init_engine(db_path: Path):
    global _engine, _SessionLocal
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        f"sqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)
    return _engine


def get_engine():
    if _engine is None:
        raise RuntimeError("db.init_engine() was not called")
    return _engine


def create_all():
    from agent_fleet import models  # noqa: F401 - registers tables

    Base.metadata.create_all(get_engine())
    _add_missing_columns()


def _add_missing_columns():
    """SQLite has no ALTER-based migration; add new columns in place."""
    from sqlalchemy import inspect, text

    from agent_fleet import models  # noqa: F401

    engine = get_engine()
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspect(conn).get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or column.primary_key:
                    continue
                ddl = column.type.compile(engine.dialect)
                default = ""
                if column.default is not None and column.default.is_scalar:
                    default = f" DEFAULT {column.default.arg!r}"
                conn.execute(
                    text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {ddl}{default}')
                )


@contextmanager
def session_scope():
    if _SessionLocal is None:
        raise RuntimeError("db.init_engine() was not called")
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
