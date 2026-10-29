from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Callable

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

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
    from agent_fleet import herdr_models, paseo_models, task_models  # noqa: F401 - feature tables

    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_missing_columns()
    run_migrations(engine)


def _init_migrations_table(dbapi_conn: sqlite3.Connection):
    dbapi_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _get_applied_migration_versions(dbapi_conn: sqlite3.Connection) -> set[int]:
    _init_migrations_table(dbapi_conn)
    rows = dbapi_conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def _record_migration(dbapi_conn: sqlite3.Connection, version: int, name: str):
    now_str = datetime.now(timezone.utc).isoformat()
    dbapi_conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
        (version, name, now_str),
    )
    dbapi_conn.execute(f"PRAGMA user_version = {version}")


def _migration_0001_agent_config_unique_and_fk(dbapi_conn: sqlite3.Connection):
    """Ensure AgentConfiguration.installation_id has UNIQUE constraint,

    AgentInstallation.config_id has FK to AgentConfiguration.id,
    and deduplicate any historical multiple configs per installation.
    """
    tables = {
        row[0]
        for row in dbapi_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "agent_configurations" not in tables or "agent_installations" not in tables:
        return

    # 1. Deduplicate historical agent_configurations: keep latest collected_at, tie-break on id desc
    dbapi_conn.execute(
        """
        DELETE FROM agent_configurations
        WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY installation_id
                    ORDER BY collected_at DESC, id DESC
                ) AS rn
                FROM agent_configurations
            ) WHERE rn = 1
        )
        """
    )

    # 2. Re-point agent_installations.config_id to the surviving config_id, or NULL
    dbapi_conn.execute(
        """
        UPDATE agent_installations
        SET config_id = (
            SELECT id FROM agent_configurations
            WHERE agent_configurations.installation_id = agent_installations.id
        )
        WHERE id IN (SELECT installation_id FROM agent_configurations)
        """
    )
    dbapi_conn.execute(
        """
        UPDATE agent_installations
        SET config_id = NULL
        WHERE config_id IS NOT NULL
          AND config_id NOT IN (SELECT id FROM agent_configurations)
        """
    )

    # 3. Ensure unique index on agent_configurations(installation_id)
    has_unique = False
    for idx in dbapi_conn.execute("PRAGMA index_list(agent_configurations)").fetchall():
        if idx[2]:  # unique
            idx_name = idx[1]
            cols = [
                c[2]
                for c in dbapi_conn.execute(f'PRAGMA index_info("{idx_name}")').fetchall()
            ]
            if cols == ["installation_id"]:
                has_unique = True
                break
    if not has_unique:
        dbapi_conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_config_installation_id ON agent_configurations(installation_id)"
        )

    # 4. Check if foreign key on agent_installations(config_id) -> agent_configurations(id) exists
    fk_rows = dbapi_conn.execute("PRAGMA foreign_key_list(agent_installations)").fetchall()
    has_fk = any(
        fk[2] == "agent_configurations" and fk[3] == "config_id" and fk[4] == "id"
        for fk in fk_rows
    )

    if not has_fk:
        orig_fk = dbapi_conn.execute("PRAGMA foreign_keys").fetchone()[0]
        try:
            dbapi_conn.execute("PRAGMA foreign_keys = OFF;")
            dbapi_conn.execute("BEGIN TRANSACTION;")

            inst_cols = [
                col[1]
                for col in dbapi_conn.execute("PRAGMA table_info(agent_installations)").fetchall()
            ]

            dbapi_conn.execute(
                """
                CREATE TABLE agent_installations_new (
                    id VARCHAR NOT NULL,
                    runtime_id VARCHAR NOT NULL,
                    agent_id VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'not_scanned',
                    detected BOOLEAN NOT NULL DEFAULT 0,
                    version VARCHAR,
                    version_raw VARCHAR,
                    exec_path VARCHAR,
                    install_method VARCHAR,
                    config_dir VARCHAR,
                    detected_at DATETIME,
                    error TEXT,
                    config_status VARCHAR NOT NULL DEFAULT 'not_scanned',
                    config_id VARCHAR,
                    PRIMARY KEY (id),
                    CONSTRAINT uq_runtime_agent UNIQUE (runtime_id, agent_id),
                    FOREIGN KEY(runtime_id) REFERENCES runtimes (id) ON DELETE CASCADE,
                    CONSTRAINT fk_agent_installation_config_id FOREIGN KEY(config_id) REFERENCES agent_configurations (id) ON DELETE SET NULL
                )
                """
            )

            target_cols = [
                "id", "runtime_id", "agent_id", "status", "detected", "version",
                "version_raw", "exec_path", "install_method", "config_dir",
                "detected_at", "error", "config_status", "config_id"
            ]
            common_cols = [c for c in target_cols if c in inst_cols]
            cols_str = ", ".join(common_cols)
            dbapi_conn.execute(
                f"INSERT INTO agent_installations_new ({cols_str}) SELECT {cols_str} FROM agent_installations"
            )

            dbapi_conn.execute("DROP TABLE agent_installations;")
            dbapi_conn.execute("ALTER TABLE agent_installations_new RENAME TO agent_installations;")
            dbapi_conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_agent_installations_runtime_id ON agent_installations (runtime_id);"
            )
            dbapi_conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_agent_installations_agent_id ON agent_installations (agent_id);"
            )

            dbapi_conn.execute("COMMIT;")
        except Exception:
            dbapi_conn.execute("ROLLBACK;")
            raise
        finally:
            if orig_fk:
                dbapi_conn.execute("PRAGMA foreign_keys = ON;")

        fk_errors = dbapi_conn.execute("PRAGMA foreign_key_check;").fetchall()
        if fk_errors:
            raise RuntimeError(f"Foreign key violations found after migration: {fk_errors}")


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "0001_agent_config_unique_and_fk", _migration_0001_agent_config_unique_and_fk),
]


def run_migrations(engine=None):
    if engine is None:
        engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        dbapi_conn = raw_conn.driver_connection
        orig_iso = dbapi_conn.isolation_level
        dbapi_conn.isolation_level = None
        try:
            applied = _get_applied_migration_versions(dbapi_conn)
            for version, name, func in MIGRATIONS:
                if version not in applied:
                    logger.info("Applying schema migration %d: %s", version, name)
                    func(dbapi_conn)
                    _record_migration(dbapi_conn, version, name)
        finally:
            dbapi_conn.isolation_level = orig_iso
    finally:
        raw_conn.close()


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
