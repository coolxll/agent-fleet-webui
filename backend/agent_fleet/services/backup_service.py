"""SQLite consistent backup service with integrity check and metadata generation.

Provides atomic, consistent snapshots of SQLite databases (even under active WAL writes)
using SQLite's Online Backup API (sqlite3.Connection.backup()), verifies integrity,
computes SHA-256 checksums, and generates companion metadata files with strict file permissions (0600).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from agent_fleet.settings import Settings, get_settings
from agent_fleet.version import get_version_info

logger = logging.getLogger(__name__)


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file in streaming chunks."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def create_backup(
    src_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    filename: str | None = None,
    overwrite: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Create a verified, consistent online backup of the SQLite database.

    Args:
        src_path: Path to source SQLite database. If None, uses settings.db_path.
        output_dir: Directory to save backup. If None, uses settings.data_dir / 'backups'.
        filename: Destination filename. If None, generates timestamped filename.
        overwrite: If True, allows replacing existing backup and metadata files.
        settings: Application settings instance.

    Returns:
        dict: Metadata dictionary describing the backup.

    Raises:
        FileNotFoundError: If source database file does not exist.
        FileExistsError: If destination file exists and overwrite is False.
        RuntimeError: If SQLite integrity check fails.
        PermissionError / OSError: If filesystem permissions or disk space fail.
    """
    if settings is None:
        settings = get_settings()

    src_file = Path(src_path) if src_path is not None else settings.db_path
    if not src_file.exists():
        raise FileNotFoundError(f"Source database not found: {src_file}")

    target_dir = Path(output_dir) if output_dir is not None else (settings.data_dir / "backups")

    if not filename:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"fleet_backup_{ts}.db"
    elif not (filename.endswith(".db") or filename.endswith(".sqlite")):
        filename = f"{filename}.db"

    backup_path = target_dir / filename
    backup_name = backup_path.name
    meta_path = target_dir / f"{backup_name}.meta.json"

    if not overwrite:
        if backup_path.exists():
            raise FileExistsError(
                f"Backup file already exists: {backup_path}. Use --overwrite to replace."
            )
        if meta_path.exists():
            raise FileExistsError(
                f"Backup metadata file already exists: {meta_path}. Use --overwrite to replace."
            )

    target_dir.mkdir(parents=True, exist_ok=True)

    # Use unique temporary paths in destination directory for atomic writes
    temp_suffix = f".tmp_{uuid.uuid4().hex}"
    tmp_backup_path = target_dir / f"{backup_name}{temp_suffix}"
    tmp_meta_path = target_dir / f"{backup_name}.meta.json{temp_suffix}"

    try:
        # 1. Perform SQLite online backup
        # Source opened read-only if possible, with timeout for concurrency
        src_conn = sqlite3.connect(str(src_file), timeout=30.0)
        try:
            src_conn.execute("PRAGMA busy_timeout=30000")
            dst_conn = sqlite3.connect(str(tmp_backup_path))
            try:
                with dst_conn:
                    src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

        # Restrict permissions on backup immediately
        os.chmod(tmp_backup_path, 0o600)

        # 2. Verify integrity and extract database metadata
        check_conn = sqlite3.connect(str(tmp_backup_path))
        try:
            integrity_rows = check_conn.execute("PRAGMA integrity_check").fetchall()
            integrity_ok = len(integrity_rows) == 1 and integrity_rows[0][0] == "ok"
            if not integrity_ok:
                raise RuntimeError(
                    f"Backup integrity check failed: {[r[0] for r in integrity_rows]}"
                )

            # Extract schema_version (user_version or schema_migrations)
            uv_row = check_conn.execute("PRAGMA user_version").fetchone()
            schema_version = uv_row[0] if uv_row else 0

            table_rows = check_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            table_names = [r[0] for r in table_rows]

            if "schema_migrations" in table_names:
                mig_row = check_conn.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()
                if mig_row and mig_row[0] is not None and mig_row[0] > schema_version:
                    schema_version = int(mig_row[0])

            # Extract table counts
            table_counts: dict[str, int] = {}
            for tbl in table_names:
                escaped = tbl.replace('"', '""')
                count = check_conn.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
                table_counts[tbl] = count
        finally:
            check_conn.close()

        # 3. Compute checksum & file size
        sha256_hex = compute_sha256(tmp_backup_path)
        file_size_bytes = tmp_backup_path.stat().st_size

        # 4. App version
        app_version = get_version_info()["version"]

        created_at = datetime.now(timezone.utc).isoformat()

        metadata: dict[str, Any] = {
            "backup_name": backup_name,
            "created_at": created_at,
            "app_version": app_version,
            "schema_version": int(schema_version),
            "sha256": sha256_hex,
            "file_size_bytes": file_size_bytes,
            "integrity_ok": integrity_ok,
            "table_counts": table_counts,
        }

        # 5. Write metadata with strict 0600 permissions
        meta_json_str = json.dumps(metadata, indent=2) + "\n"
        with open(tmp_meta_path, "w", encoding="utf-8") as f:
            f.write(meta_json_str)
        os.chmod(tmp_meta_path, 0o600)

        # 6. Atomically replace final files
        os.replace(tmp_backup_path, backup_path)
        os.chmod(backup_path, 0o600)

        os.replace(tmp_meta_path, meta_path)
        os.chmod(meta_path, 0o600)

        logger.info(
            "Created consistent backup %s (size=%d bytes, sha256=%s...)",
            backup_name,
            file_size_bytes,
            sha256_hex[:8],
        )
        return metadata

    finally:
        # Clean up temporary files on error
        if tmp_backup_path.exists():
            try:
                tmp_backup_path.unlink()
            except OSError:
                pass
        if tmp_meta_path.exists():
            try:
                tmp_meta_path.unlink()
            except OSError:
                pass


def verify_backup(
    backup_path: Path | str,
    meta_path: Path | str | None = None,
) -> dict[str, Any]:
    """Verify an existing backup file's integrity and compare with its metadata.

    Args:
        backup_path: Path to the backup SQLite database file.
        meta_path: Path to the companion metadata file. If None, checks alongside.

    Returns:
        dict: Detailed verification result.
    """
    db_file = Path(backup_path)
    if not db_file.exists():
        raise FileNotFoundError(f"Backup file not found: {db_file}")

    if meta_path is None:
        meta_candidate = db_file.parent / f"{db_file.name}.meta.json"
        meta_file = meta_candidate if meta_candidate.exists() else None
    else:
        meta_file = Path(meta_path)

    # 1. Run PRAGMA integrity_check
    conn = sqlite3.connect(str(db_file))
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        integrity_ok = len(rows) == 1 and rows[0][0] == "ok"
    finally:
        conn.close()

    # 2. Compute checksum & file size
    actual_sha256 = compute_sha256(db_file)
    actual_size = db_file.stat().st_size

    checksum_ok = None
    size_ok = None
    meta_data = None

    if meta_file and meta_file.exists():
        with open(meta_file, encoding="utf-8") as f:
            meta_data = json.load(f)
        checksum_ok = meta_data.get("sha256") == actual_sha256
        size_ok = meta_data.get("file_size_bytes") == actual_size

    return {
        "backup_path": str(db_file),
        "integrity_ok": integrity_ok,
        "sha256": actual_sha256,
        "file_size_bytes": actual_size,
        "checksum_ok": checksum_ok,
        "size_ok": size_ok,
        "metadata": meta_data,
    }
