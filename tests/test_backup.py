"""Unit tests for SQLite consistent backup service and CLI command."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from agent_fleet.cli import main
from agent_fleet.services.backup_service import (
    compute_sha256,
    create_backup,
    verify_backup,
)
from agent_fleet.settings import get_settings


def test_backup_standard_mode(tmp_path: Path):
    """Test backup creation on a database in standard (DELETE journal) mode."""
    src_db = tmp_path / "standard.db"
    conn = sqlite3.connect(src_db)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA user_version=42")
    conn.execute("CREATE TABLE items (id INT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO items VALUES (1, 'apple'), (2, 'banana')")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "backups"
    meta = create_backup(
        src_path=src_db,
        output_dir=backup_dir,
        filename="standard_backup.db",
    )

    backup_file = backup_dir / "standard_backup.db"
    meta_file = backup_dir / "standard_backup.db.meta.json"

    assert backup_file.exists()
    assert meta_file.exists()
    assert meta["backup_name"] == "standard_backup.db"
    assert meta["schema_version"] == 42
    assert meta["integrity_ok"] is True
    assert meta["table_counts"] == {"items": 2}
    assert meta["file_size_bytes"] == backup_file.stat().st_size
    assert meta["sha256"] == compute_sha256(backup_file)

    # Verify file permissions are 0600
    assert oct(backup_file.stat().st_mode & 0o777) == "0o600"
    assert oct(meta_file.stat().st_mode & 0o777) == "0o600"


def test_backup_wal_mode_and_metadata(tmp_path: Path):
    """Test backup creation with active WAL mode and schema_migrations tracking."""
    src_db = tmp_path / "fleet.db"
    conn = sqlite3.connect(src_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA user_version=1")
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.execute("INSERT INTO schema_migrations VALUES (5, '0005_test', '2026-10-29T00:00:00Z')")
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)")
    conn.execute("INSERT INTO users VALUES ('u1', 'alice@example.com'), ('u2', 'bob@example.com')")
    conn.commit()

    assert (tmp_path / "fleet.db-wal").exists()

    backup_dir = tmp_path / "backups"
    meta = create_backup(
        src_path=src_db,
        output_dir=backup_dir,
        filename="wal_backup",  # tests auto-appending .db
    )
    conn.close()

    backup_file = backup_dir / "wal_backup.db"
    meta_file = backup_dir / "wal_backup.db.meta.json"

    assert backup_file.exists()
    assert meta_file.exists()
    assert meta["backup_name"] == "wal_backup.db"
    assert meta["schema_version"] == 5
    assert meta["integrity_ok"] is True
    assert meta["table_counts"] == {"schema_migrations": 1, "users": 2}

    # Verify backup contents independently
    check = verify_backup(backup_file)
    assert check["integrity_ok"] is True
    assert check["checksum_ok"] is True
    assert check["size_ok"] is True


def test_refuse_overwrite_without_flag(tmp_path: Path):
    """Test that existing backup is not overwritten unless overwrite=True."""
    src_db = tmp_path / "test.db"
    conn = sqlite3.connect(src_db)
    conn.execute("CREATE TABLE t (id INT)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "backups"
    create_backup(src_db, backup_dir, "existing.db")

    # Second backup without overwrite should raise FileExistsError
    with pytest.raises(FileExistsError, match="Backup file already exists"):
        create_backup(src_db, backup_dir, "existing.db", overwrite=False)

    # Second backup with overwrite should succeed
    meta2 = create_backup(src_db, backup_dir, "existing.db", overwrite=True)
    assert meta2["backup_name"] == "existing.db"


def test_concurrent_read_write_during_backup(tmp_path: Path):
    """Test consistent backup while concurrent threads continuously write in WAL mode."""
    src_db = tmp_path / "concurrency.db"
    conn = sqlite3.connect(src_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE logs (seq INT PRIMARY KEY, message TEXT)")
    conn.commit()
    conn.close()

    stop_event = threading.Event()
    writer_count = [0]
    errors = []

    def writer_worker():
        try:
            w_conn = sqlite3.connect(src_db, timeout=30.0)
            w_conn.execute("PRAGMA busy_timeout=30000")
            i = 0
            while not stop_event.is_set():
                w_conn.execute("INSERT INTO logs VALUES (?, ?)", (i, f"log_msg_{i}"))
                w_conn.commit()
                i += 1
                writer_count[0] = i
                time.sleep(0.005)
            w_conn.close()
        except Exception as e:
            errors.append(e)

    writer_thread = threading.Thread(target=writer_worker, daemon=True)
    writer_thread.start()

    # Let writer insert some initial rows
    while writer_count[0] < 5:
        time.sleep(0.01)

    backup_dir = tmp_path / "backups"
    meta = create_backup(
        src_path=src_db,
        output_dir=backup_dir,
        filename="concurrent_backup.db",
    )

    stop_event.set()
    writer_thread.join(timeout=5.0)

    assert not errors, f"Writer thread failed: {errors}"
    assert meta["integrity_ok"] is True
    assert meta["table_counts"]["logs"] >= 5

    # Destination database should be completely valid and uncorrupted
    backup_file = backup_dir / "concurrent_backup.db"
    b_conn = sqlite3.connect(backup_file)
    integrity = b_conn.execute("PRAGMA integrity_check").fetchall()
    assert integrity == [("ok",)]
    count = b_conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    assert count == meta["table_counts"]["logs"]
    b_conn.close()


def test_missing_source_db(tmp_path: Path):
    """Test error handling when source database does not exist."""
    missing_src = tmp_path / "non_existent.db"
    with pytest.raises(FileNotFoundError, match="Source database not found"):
        create_backup(src_path=missing_src, output_dir=tmp_path / "out")


def test_cli_backup_command(tmp_path: Path, monkeypatch, capsys):
    """Test agent-fleet backup command line interface."""
    db_file = tmp_path / "fleet_cli.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE sample (id INT, val TEXT)")
    conn.execute("INSERT INTO sample VALUES (1, 'cli_test')")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "cli_backups"

    # Monkeypatch settings to point to our test DB and clear cached settings
    monkeypatch.setenv("AGENT_FLEET_DB_PATH", str(db_file))
    monkeypatch.setenv("AGENT_FLEET_INVENTORY_PATHS", "[]")
    monkeypatch.setenv("AGENT_FLEET_INCLUDE_LOCAL_RUNTIME", "false")
    get_settings.cache_clear()

    try:
        # 1. Run backup via CLI
        main(["backup", "--output-dir", str(backup_dir), "--filename", "cli_bk.db"])
        captured = capsys.readouterr()
        assert "cli_bk.db" in captured.out

        result = json.loads(captured.out)
        assert result["backup_name"] == "cli_bk.db"
        assert result["integrity_ok"] is True
        assert result["table_counts"] == {"sample": 1}

        # 2. Re-run without --overwrite should fail with code 1
        with pytest.raises(SystemExit) as exc_info:
            main(["backup", "--output-dir", str(backup_dir), "--filename", "cli_bk.db"])
        assert exc_info.value.code == 1

        # 3. Re-run with --overwrite should succeed
        main(["backup", "--output-dir", str(backup_dir), "--filename", "cli_bk.db", "--overwrite"])
        captured2 = capsys.readouterr()
        result2 = json.loads(captured2.out)
        assert result2["backup_name"] == "cli_bk.db"
    finally:
        get_settings.cache_clear()


def test_default_filename_and_source_immutability(tmp_path: Path):
    """Test default timestamped filename generation and that source db is never mutated."""
    src_db = tmp_path / "immutable.db"
    conn = sqlite3.connect(src_db)
    conn.execute("CREATE TABLE records (id INT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO records VALUES (1, 'orig_1'), (2, 'orig_2')")
    conn.commit()
    conn.close()

    src_stat_before = src_db.stat()
    src_sha_before = compute_sha256(src_db)

    backup_dir = tmp_path / "auto_backups"
    meta = create_backup(
        src_path=src_db,
        output_dir=backup_dir,
    )

    assert meta["backup_name"].startswith("fleet_backup_")
    assert meta["backup_name"].endswith(".db")
    assert (backup_dir / meta["backup_name"]).exists()
    assert (backup_dir / f"{meta['backup_name']}.meta.json").exists()

    # Verify source database was not modified
    src_stat_after = src_db.stat()
    src_sha_after = compute_sha256(src_db)
    assert src_stat_before.st_size == src_stat_after.st_size
    assert src_sha_before == src_sha_after


def test_verify_backup_detects_tampering(tmp_path: Path):
    """Test verify_backup correctly detects checksum and size mismatch if file is tampered."""
    src_db = tmp_path / "tamper.db"
    conn = sqlite3.connect(src_db)
    conn.execute("CREATE TABLE test (v TEXT)")
    conn.execute("INSERT INTO test VALUES ('ok')")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "tamper_backups"
    meta = create_backup(src_db, backup_dir, "clean.db")
    backup_file = backup_dir / "clean.db"

    # Clean verification
    res = verify_backup(backup_file)
    assert res["integrity_ok"] is True
    assert res["checksum_ok"] is True
    assert res["size_ok"] is True

    # Tamper with the backup file bytes
    with open(backup_file, "ab") as f:
        f.write(b"corrupt")

    # Verification should detect mismatch
    res_tampered = verify_backup(backup_file)
    assert res_tampered["checksum_ok"] is False
    assert res_tampered["size_ok"] is False

