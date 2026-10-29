"""SQLite offline restore service with verification, safety snapshot, and rollback protection.

Provides a robust, atomic offline restore workflow for SQLite databases:
1. Pre-restore validation: verifies backup file, companion metadata (.meta.json),
   SHA-256 checksum, SQLite integrity (PRAGMA integrity_check), and schema version compatibility.
2. Offline safety check: ensures the target database is not locked by an active process
   and has no active WAL activity (unless --force is provided).
3. Pre-restore safety snapshot: creates a timestamped copy (<target>.pre_restore_<ts>.bak)
   before touching the target database.
4. Atomic restore & rollback: replaces target database atomically; if any step fails,
   automatically restores the safety copy and cleans up temporary files.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Callable
import uuid

from agent_fleet.services.backup_service import compute_sha256
from agent_fleet.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class RestoreError(Exception):
    """Base exception for database restore failures."""


class BackupValidationError(RestoreError):
    """Raised when backup validation (integrity, checksum, existence) fails."""


class SchemaIncompatibleError(RestoreError):
    """Raised when backup schema version exceeds the application's supported schema."""


class DatabaseActiveError(RestoreError):
    """Raised when the target database is locked or has active WAL activity without --force."""


def _is_pid_alive(pid: int) -> bool:
    """Check if process with given PID is currently alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def get_max_known_schema_version() -> int:
    """Return the maximum known schema version supported by the current codebase."""
    try:
        from agent_fleet.db import MIGRATIONS

        if MIGRATIONS:
            return max(v for v, _, _ in MIGRATIONS)
    except Exception:
        pass
    return 1


def inspect_sqlite_database(db_path: Path) -> tuple[bool, int, dict[str, int]]:
    """Inspect SQLite database file: returns (integrity_ok, schema_version, table_counts).

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        tuple of (integrity_ok: bool, schema_version: int, table_counts: dict[str, int])
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        try:
            # 1. Run PRAGMA integrity_check
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            integrity_ok = len(rows) == 1 and rows[0][0] == "ok"
            if not integrity_ok:
                return False, 0, {}

            # 2. Extract schema version (PRAGMA user_version or schema_migrations table)
            uv_row = conn.execute("PRAGMA user_version").fetchone()
            schema_version = uv_row[0] if uv_row else 0

            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            table_names = [r[0] for r in table_rows]

            if "schema_migrations" in table_names:
                mig_row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                if mig_row and mig_row[0] is not None and mig_row[0] > schema_version:
                    schema_version = int(mig_row[0])

            # 3. Extract table counts
            table_counts: dict[str, int] = {}
            for tbl in table_names:
                escaped = tbl.replace('"', '""')
                count = conn.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
                table_counts[tbl] = count

            return integrity_ok, int(schema_version), table_counts
        finally:
            conn.close()
    except (sqlite3.DatabaseError, sqlite3.OperationalError):
        return False, 0, {}


def validate_backup_for_restore(
    backup_path: Path | str,
    meta_path: Path | str | None = None,
    max_supported_version: int | None = None,
) -> dict[str, Any]:
    """Validate a backup file and its metadata prior to restoration.

    Args:
        backup_path: Path to the backup SQLite database file.
        meta_path: Optional explicit path to companion metadata file (.meta.json).
        max_supported_version: Maximum schema version accepted (defaults to codebase max).

    Returns:
        dict: Validation report containing metadata, checksum, and table counts.

    Raises:
        FileNotFoundError: If backup file or metadata file does not exist.
        BackupValidationError: If checksum mismatch or PRAGMA integrity_check fails.
        SchemaIncompatibleError: If backup schema version exceeds max supported version.
    """
    backup_file = Path(backup_path)
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    if meta_path is None:
        meta_file = backup_file.parent / f"{backup_file.name}.meta.json"
    else:
        meta_file = Path(meta_path)

    if not meta_file.exists():
        raise FileNotFoundError(
            f"Backup metadata file not found: {meta_file}. "
            "A valid companion .meta.json file is required for verified restore."
        )

    try:
        with open(meta_file, encoding="utf-8") as f:
            meta_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise BackupValidationError(f"Failed to read or parse metadata file {meta_file}: {e}") from e

    # 1. Verify SHA-256 checksum
    actual_sha256 = compute_sha256(backup_file)
    expected_sha256 = meta_data.get("sha256")
    if not expected_sha256:
        raise BackupValidationError(f"Metadata file {meta_file} is missing 'sha256' field.")
    if actual_sha256 != expected_sha256:
        raise BackupValidationError(
            f"Checksum mismatch for {backup_file.name}: "
            f"expected {expected_sha256}, got {actual_sha256}. "
            "The backup file may be corrupted or tampered with."
        )

    # 2. SQLite PRAGMA integrity_check & schema extraction
    integrity_ok, db_schema_version, table_counts = inspect_sqlite_database(backup_file)
    if not integrity_ok:
        raise BackupValidationError(
            f"SQLite PRAGMA integrity_check failed for backup file: {backup_file}"
        )

    # 3. Schema compatibility verification
    meta_schema_version = meta_data.get("schema_version")
    if meta_schema_version is not None:
        effective_schema_version = max(int(meta_schema_version), db_schema_version)
    else:
        effective_schema_version = db_schema_version

    max_version = (
        max_supported_version
        if max_supported_version is not None
        else get_max_known_schema_version()
    )

    if effective_schema_version > max_version:
        raise SchemaIncompatibleError(
            f"Backup schema version ({effective_schema_version}) exceeds the maximum supported "
            f"schema version ({max_version}) of this Agent Fleet installation. "
            "Please upgrade Agent Fleet before restoring this backup."
        )

    return {
        "valid": True,
        "backup_path": str(backup_file),
        "meta_path": str(meta_file),
        "sha256": actual_sha256,
        "schema_version": effective_schema_version,
        "max_supported_version": max_version,
        "table_counts": table_counts,
        "metadata": meta_data,
    }


def check_target_offline(
    target_path: Path,
    settings: Settings | None = None,
) -> tuple[bool, str | None]:
    """Check if the target SQLite database appears to be offline and safe to replace.

    Checks:
    1. Active PID files for running Agent Fleet server/controller processes.
    2. Active directory file locks (.agent_fleet.lock).
    3. Active WAL file with pending frames.
    4. SQLite transaction locks and WAL checkpoint quiescence.

    Args:
        target_path: Path to the target SQLite database file.
        settings: Optional Settings instance.

    Returns:
        tuple of (is_offline: bool, failure_reason: str | None)
    """
    if not target_path.exists():
        return True, None

    # Check 1: Process PID file check
    pid_files = [target_path.parent / "agent_fleet.pid"]
    if settings is not None:
        s_pid = settings.data_dir / "agent_fleet.pid"
        if s_pid not in pid_files:
            pid_files.append(s_pid)
    else:
        try:
            s_pid = get_settings().data_dir / "agent_fleet.pid"
            if s_pid not in pid_files:
                pid_files.append(s_pid)
        except Exception:
            pass

    for pf in pid_files:
        if pf.exists():
            try:
                content = pf.read_text(encoding="utf-8").strip()
                if content:
                    pid = int(content.split()[0])
                    if _is_pid_alive(pid):
                        return (
                            False,
                            f"Active Agent Fleet server process detected (PID {pid} recorded in {pf.name}). "
                            "Stop the server process before restoring.",
                        )
            except (ValueError, OSError) as e:
                logger.debug("Failed to read PID file %s: %s", pf, e)

    # Check 2: Directory lock file
    lock_file = target_path.parent / ".agent_fleet.lock"
    if lock_file.exists():
        try:
            import fcntl

            with open(lock_file, "r+") as lf:
                try:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                except (BlockingIOError, OSError):
                    return (
                        False,
                        f"Target database directory has an active file lock ({lock_file.name}).",
                    )
        except (ImportError, OSError):
            pass

    # Check 3: Active WAL file check (performed before connecting so connect does not alter/checkpoint WAL)
    wal_file = target_path.with_name(f"{target_path.name}-wal")
    if wal_file.exists() and wal_file.stat().st_size > 0:
        return (
            False,
            f"Target database has an active WAL file ({wal_file.name}, {wal_file.stat().st_size} bytes). "
            "The database may have uncheckpointed writes or an active background process.",
        )

    # Check 4: Exclusive lock test & WAL quiescence
    try:
        conn = sqlite3.connect(str(target_path), timeout=0.2)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("COMMIT")

            # In WAL mode, verify checkpoint truncate is not busy
            row = conn.execute("PRAGMA journal_mode").fetchone()
            if row and str(row[0]).lower() == "wal":
                cp_row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if cp_row and cp_row[0] == 1:
                    return (
                        False,
                        "Target database has active connections holding the WAL file busy.",
                    )
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        return False, f"Target database is currently locked by another active process ({e})"

    return True, None


def restore_database(
    backup_path: Path | str,
    target_path: Path | str | None = None,
    meta_path: Path | str | None = None,
    force: bool = False,
    max_supported_version: int | None = None,
    settings: Settings | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Perform verified offline restore of an SQLite database with rollback safety.

    Args:
        backup_path: Path to backup SQLite database file (.db).
        target_path: Target database destination path. If None, uses settings.db_path.
        meta_path: Optional explicit metadata file path.
        force: If True, bypasses target database lock / active WAL check.
        max_supported_version: Override for maximum supported schema version.
        settings: Application settings instance.
        progress_callback: Optional callable for step-by-step progress messages.

    Returns:
        dict: Restoration summary report.

    Raises:
        FileNotFoundError: If backup file or metadata file is missing.
        BackupValidationError: If checksum or integrity check fails.
        SchemaIncompatibleError: If backup schema version exceeds supported max.
        DatabaseActiveError: If target database is locked/active and force=False.
        RestoreError: If restoration or rollback encounters an unrecoverable error.
    """
    def _notify(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    if settings is None:
        settings = get_settings()

    backup_file = Path(backup_path).resolve()
    target_file = Path(target_path).resolve() if target_path is not None else settings.db_path.resolve()

    # Step 1: Pre-restore Validation
    _notify("Step 1/4: Validating backup file integrity and metadata...")
    val_report = validate_backup_for_restore(
        backup_file, meta_path=meta_path, max_supported_version=max_supported_version
    )
    _notify(
        f"  [OK] SHA-256 verified ({val_report['sha256'][:12]}...)"
    )
    _notify("  [OK] PRAGMA integrity_check passed")
    _notify(
        f"  [OK] Schema compatibility check passed "
        f"(backup v{val_report['schema_version']} <= app max v{val_report['max_supported_version']})"
    )

    # Step 2: Offline Safety Check
    _notify("Step 2/4: Checking target database offline state...")
    is_offline, reason = check_target_offline(target_file, settings=settings)
    if not is_offline:
        if not force:
            raise DatabaseActiveError(
                f"Target database safety check failed: {reason} "
                "Ensure all Agent Fleet services are stopped, or use --force to override."
            )
        _notify(f"  [WARN] Bypassing offline check (--force specified): {reason}")
    else:
        _notify("  [OK] Target database is offline and unlocked")

    # Step 3: Create Pre-Restore Safety Snapshot via SQLite Online Backup API
    _notify("Step 3/4: Creating safety pre-restore snapshot...")
    target_file.parent.mkdir(parents=True, exist_ok=True)
    pre_restore_bak: Path | None = None
    pre_restore_wal: Path | None = None
    target_wal = target_file.with_name(f"{target_file.name}-wal")
    target_shm = target_file.with_name(f"{target_file.name}-shm")

    if target_file.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        pre_restore_bak = target_file.parent / f"{target_file.name}.pre_restore_{ts}.bak"
        if pre_restore_bak.exists():
            pre_restore_bak = (
                target_file.parent / f"{target_file.name}.pre_restore_{ts}_{uuid.uuid4().hex[:6]}.bak"
            )

        # Preserve a copy of WAL file BEFORE any SQLite connection is opened,
        # in case backup() checkpoints/removes the WAL or target is in DELETE mode.
        # Note: Do not name it with "-wal" suffix to prevent SQLite from treating it
        # as pre_restore_bak's own active WAL file during backup connection.
        if target_wal.exists():
            pre_restore_wal = target_file.parent / f"{pre_restore_bak.name}.wal_backup"
            shutil.copy2(target_wal, pre_restore_wal)
            os.chmod(pre_restore_wal, 0o600)

        try:
            # Use SQLite Online Backup API for atomic, consistent snapshot
            src_conn = sqlite3.connect(str(target_file), timeout=10.0)
            try:
                dst_conn = sqlite3.connect(str(pre_restore_bak))
                try:
                    with dst_conn:
                        src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
            finally:
                src_conn.close()

            # Verify integrity of snapshot
            bak_ok, _, _ = inspect_sqlite_database(pre_restore_bak)
            if not bak_ok:
                raise BackupValidationError(
                    f"Generated pre-restore safety snapshot failed integrity check: {pre_restore_bak}"
                )

            os.chmod(pre_restore_bak, 0o600)
        except Exception as e:
            # Fallback to direct copy if target DB is not valid SQLite (e.g. raw corrupted bytes)
            logger.warning(
                "SQLite backup API failed for pre-restore snapshot (%s), falling back to raw copy", e
            )
            shutil.copy2(target_file, pre_restore_bak)
            os.chmod(pre_restore_bak, 0o600)

        _notify(f"  [OK] Safety backup created: {pre_restore_bak.name}")
    else:
        _notify("  [INFO] Target database does not exist yet; no pre-restore snapshot needed.")

    # Step 4: Atomic Database Restore with Rollback Protection
    _notify("Step 4/4: Executing atomic database swap...")
    temp_suffix = f".tmp_restore_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    tmp_target = target_file.parent / f"{target_file.name}{temp_suffix}"

    try:
        # 1. Ensure replacement DB is staged and verified in a temporary file in the same directory
        shutil.copy2(backup_file, tmp_target)
        os.chmod(tmp_target, 0o600)

        # Re-verify integrity of the freshly written temporary file
        temp_ok, _, _ = inspect_sqlite_database(tmp_target)
        if not temp_ok:
            raise BackupValidationError("Temporary copy failed PRAGMA integrity_check before swap.")

        # 2. Acquire exclusive lock / ensure target is quiescent
        if target_file.exists():
            try:
                lock_conn = sqlite3.connect(str(target_file), timeout=1.0)
                try:
                    lock_conn.execute("BEGIN EXCLUSIVE")
                    # Checkpoint to truncate WAL if in WAL mode
                    try:
                        lock_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    except Exception:
                        pass
                finally:
                    lock_conn.close()
            except sqlite3.OperationalError as e:
                if not force:
                    raise DatabaseActiveError(
                        f"Target database became locked by another process during restore preparation ({e})"
                    )
                _notify(f"  [WARN] Proceeding with swap despite lock (--force specified): {e}")

        # 3. Perform atomic replace
        os.replace(tmp_target, target_file)
        os.chmod(target_file, 0o600)

        # 4. Only after successful replace, clean up legacy WAL and SHM sidecars safely
        if target_wal.exists():
            try:
                target_wal.unlink()
            except OSError as e:
                logger.warning("Could not unlink legacy WAL file %s: %s", target_wal, e)
        if target_shm.exists():
            try:
                target_shm.unlink()
            except OSError as e:
                logger.warning("Could not unlink legacy SHM file %s: %s", target_shm, e)

        _notify(f"  [OK] Successfully restored database to {target_file}")

    except Exception as exc:
        logger.error("Restore operation failed: %s. Initiating automatic rollback...", exc)
        _notify(f"  [FAIL] Restore failed ({exc}). Initiating automatic rollback...")

        # Remove temporary swap file
        if tmp_target.exists():
            try:
                tmp_target.unlink()
            except OSError:
                pass

        # Automatic rollback to pre-restore snapshot
        if pre_restore_bak and pre_restore_bak.exists():
            try:
                shutil.copy2(pre_restore_bak, target_file)
                os.chmod(target_file, 0o600)
                if pre_restore_wal and pre_restore_wal.exists():
                    shutil.copy2(pre_restore_wal, target_wal)
                    os.chmod(target_wal, 0o600)
                _notify("  [ROLLBACK] Target database successfully rolled back to pre-restore snapshot.")
                logger.info("Rollback completed successfully from %s", pre_restore_bak)
            except Exception as rb_exc:
                logger.critical("Fatal: Rollback failed: %s", rb_exc)
                raise RestoreError(
                    f"Restore failed ({exc}) and automatic rollback also failed ({rb_exc}). "
                    f"Safety snapshot is preserved at {pre_restore_bak}."
                ) from exc
        elif not pre_restore_bak and target_file.exists():
            try:
                target_file.unlink()
            except OSError:
                pass

        raise exc

    return {
        "status": "success",
        "backup_path": str(backup_file),
        "target_path": str(target_file),
        "pre_restore_backup": str(pre_restore_bak) if pre_restore_bak else None,
        "schema_version": val_report["schema_version"],
        "table_counts": val_report["table_counts"],
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }
