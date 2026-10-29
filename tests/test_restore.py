"""Unit tests for SQLite offline restore service and CLI command."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest

from agent_fleet.cli import main
from agent_fleet.services.backup_service import create_backup
from agent_fleet.services.restore_service import (
    BackupValidationError,
    DatabaseActiveError,
    RestoreError,
    SchemaIncompatibleError,
    check_target_offline,
    get_max_known_schema_version,
    inspect_sqlite_database,
    restore_database,
    validate_backup_for_restore,
)
from agent_fleet.settings import get_settings


def _create_sample_db(db_path: Path, rows: list[tuple[int, str]], schema_version: int = 1):
    """Helper to create and populate a sample SQLite database."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute(f"PRAGMA user_version={schema_version}")
    conn.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO users VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def test_restore_success_flow(tmp_path: Path):
    """Test standard restore: backup -> alter target -> restore -> verify original data."""
    src_db = tmp_path / "original.db"
    _create_sample_db(src_db, [(1, "alice"), (2, "bob")], schema_version=1)

    backup_dir = tmp_path / "backups"
    meta = create_backup(
        src_path=src_db,
        output_dir=backup_dir,
        filename="fleet_backup_test.db",
    )
    backup_file = backup_dir / "fleet_backup_test.db"
    assert backup_file.exists()

    # Alter the database (simulate post-backup writes / corruption / bad upgrade)
    target_db = tmp_path / "fleet.db"
    _create_sample_db(target_db, [(1, "alice"), (2, "bob"), (3, "corrupted_record")], schema_version=1)

    progress_messages = []

    res = restore_database(
        backup_path=backup_file,
        target_path=target_db,
        progress_callback=progress_messages.append,
    )

    assert res["status"] == "success"
    assert res["schema_version"] == 1
    assert res["table_counts"]["users"] == 2
    assert res["pre_restore_backup"] is not None
    assert Path(res["pre_restore_backup"]).exists()

    # Verify target db is restored to original state (only 2 records)
    conn = sqlite3.connect(str(target_db))
    rows = conn.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    conn.close()
    assert rows == [(1, "alice"), (2, "bob")]

    # Verify pre_restore backup has the 3 records
    pre_bak = Path(res["pre_restore_backup"])
    conn_pre = sqlite3.connect(str(pre_bak))
    pre_rows = conn_pre.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    conn_pre.close()
    assert len(pre_rows) == 3

    # Check progress callbacks were invoked
    assert any("Step 1/4" in m for m in progress_messages)
    assert any("Step 4/4" in m for m in progress_messages)

    # Verify restored permissions are 0600
    assert oct(target_db.stat().st_mode & 0o777) == "0o600"
    assert oct(pre_bak.stat().st_mode & 0o777) == "0o600"


def test_restore_target_does_not_exist(tmp_path: Path):
    """Test restoring when target database does not exist initially."""
    src_db = tmp_path / "src.db"
    _create_sample_db(src_db, [(10, "ten")])

    backup_dir = tmp_path / "backups"
    create_backup(src_db, backup_dir, "new_install.db")
    backup_file = backup_dir / "new_install.db"

    target_db = tmp_path / "non_existent_yet" / "fleet.db"
    assert not target_db.exists()

    res = restore_database(
        backup_path=backup_file,
        target_path=target_db,
    )

    assert res["status"] == "success"
    assert res["pre_restore_backup"] is None
    assert target_db.exists()

    conn = sqlite3.connect(str(target_db))
    rows = conn.execute("SELECT id, name FROM users").fetchall()
    conn.close()
    assert rows == [(10, "ten")]


def test_restore_missing_backup_or_metadata(tmp_path: Path):
    """Test failure when backup file or companion metadata file is missing."""
    target_db = tmp_path / "fleet.db"
    _create_sample_db(target_db, [(1, "one")])

    non_existent = tmp_path / "ghost.db"
    with pytest.raises(FileNotFoundError, match="Backup file not found"):
        restore_database(non_existent, target_path=target_db)

    # Backup file exists but metadata is missing
    backup_only = tmp_path / "no_meta.db"
    _create_sample_db(backup_only, [(2, "two")])
    with pytest.raises(FileNotFoundError, match="Backup metadata file not found"):
        restore_database(backup_only, target_path=target_db)


def test_restore_tampered_checksum(tmp_path: Path):
    """Test rejection when backup checksum does not match companion metadata."""
    src_db = tmp_path / "src.db"
    _create_sample_db(src_db, [(1, "valid")])

    backup_dir = tmp_path / "backups"
    create_backup(src_db, backup_dir, "tampered.db")
    backup_file = backup_dir / "tampered.db"

    # Tamper with backup file by appending bytes
    with open(backup_file, "ab") as f:
        f.write(b"corrupted_tamper_bytes")

    target_db = tmp_path / "target.db"
    _create_sample_db(target_db, [(99, "keep_me")])

    with pytest.raises(BackupValidationError, match="Checksum mismatch"):
        restore_database(backup_file, target_path=target_db)

    # Target db must be completely untouched
    conn = sqlite3.connect(str(target_db))
    rows = conn.execute("SELECT id, name FROM users").fetchall()
    conn.close()
    assert rows == [(99, "keep_me")]


def test_restore_corrupted_sqlite_file(tmp_path: Path):
    """Test rejection when backup file fails PRAGMA integrity_check."""
    bad_db = tmp_path / "corrupt.db"
    # Write 1000 zero bytes to make an invalid SQLite file
    bad_db.write_bytes(b"\x00" * 1024)

    meta_file = tmp_path / "corrupt.db.meta.json"
    import hashlib

    hasher = hashlib.sha256()
    hasher.update(b"\x00" * 1024)
    meta_file.write_text(
        json.dumps(
            {
                "backup_name": "corrupt.db",
                "sha256": hasher.hexdigest(),
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )

    target_db = tmp_path / "target.db"
    _create_sample_db(target_db, [(1, "initial")])

    with pytest.raises(BackupValidationError, match="PRAGMA integrity_check failed"):
        restore_database(bad_db, target_path=target_db)


def test_restore_schema_incompatible_rejection(tmp_path: Path):
    """Test rejection when backup schema version is greater than application max."""
    src_db = tmp_path / "future.db"
    _create_sample_db(src_db, [(1, "future")], schema_version=99)

    backup_dir = tmp_path / "backups"
    create_backup(src_db, backup_dir, "future_backup.db")
    backup_file = backup_dir / "future_backup.db"

    target_db = tmp_path / "target.db"
    _create_sample_db(target_db, [(1, "current")])

    current_max = get_max_known_schema_version()
    assert current_max < 99

    with pytest.raises(SchemaIncompatibleError, match="exceeds the maximum supported schema version"):
        restore_database(
            backup_file,
            target_path=target_db,
            max_supported_version=current_max,
        )

    # Target db must remain untouched
    conn = sqlite3.connect(str(target_db))
    rows = conn.execute("SELECT id, name FROM users").fetchall()
    conn.close()
    assert rows == [(1, "current")]


def test_restore_offline_check_and_force(tmp_path: Path):
    """Test that active database lock or WAL presence is detected and can be bypassed with force=True."""
    src_db = tmp_path / "src.db"
    _create_sample_db(src_db, [(1, "source")])

    backup_dir = tmp_path / "backups"
    create_backup(src_db, backup_dir, "offline_test.db")
    backup_file = backup_dir / "offline_test.db"

    target_db = tmp_path / "target.db"
    _create_sample_db(target_db, [(2, "target")])

    # Simulate active WAL file with data
    wal_file = tmp_path / "target.db-wal"
    wal_file.write_bytes(b"active_wal_data_frame")

    # Offline check should report not offline
    is_offline, reason = check_target_offline(target_db)
    assert is_offline is False
    assert "active WAL file" in reason

    # Restore without force must fail
    with pytest.raises(DatabaseActiveError, match="active WAL file"):
        restore_database(backup_file, target_path=target_db, force=False)

    # Restore with force must succeed
    res = restore_database(backup_file, target_path=target_db, force=True)
    assert res["status"] == "success"

    # Wal file should have been cleaned up
    assert not wal_file.exists()

    conn = sqlite3.connect(str(target_db))
    rows = conn.execute("SELECT id, name FROM users").fetchall()
    conn.close()
    assert rows == [(1, "source")]


def test_restore_rollback_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test that if atomic swap fails, target DB is rolled back to original state."""
    src_db = tmp_path / "src.db"
    _create_sample_db(src_db, [(1, "backup_val")])

    backup_dir = tmp_path / "backups"
    create_backup(src_db, backup_dir, "rollback_test.db")
    backup_file = backup_dir / "rollback_test.db"

    target_db = tmp_path / "target.db"
    _create_sample_db(target_db, [(999, "original_target_val")])

    # Monkeypatch os.replace to simulate an unexpected I/O error during the swap
    def fake_replace(src, dst):
        raise OSError("Simulated disk replacement failure")

    monkeypatch.setattr(os, "replace", fake_replace)

    with pytest.raises(OSError, match="Simulated disk replacement failure"):
        restore_database(backup_file, target_path=target_db)

    # Verify rollback: target_db must still exist and contain original data
    assert target_db.exists()
    conn = sqlite3.connect(str(target_db))
    rows = conn.execute("SELECT id, name FROM users").fetchall()
    conn.close()
    assert rows == [(999, "original_target_val")]

    # Check that safety backup was created and still exists
    pre_baks = list(tmp_path.glob("target.db.pre_restore_*.bak"))
    assert len(pre_baks) == 1


def test_cli_restore_command(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test CLI restore subcommand execution and output."""
    src_db = tmp_path / "cli_src.db"
    _create_sample_db(src_db, [(42, "cli_success")])

    backup_dir = tmp_path / "backups"
    create_backup(src_db, backup_dir, "cli_backup.db")
    backup_file = backup_dir / "cli_backup.db"

    target_db = tmp_path / "cli_target.db"
    _create_sample_db(target_db, [(1, "cli_old")])

    # Execute CLI restore
    main(["restore", str(backup_file), "--target-db", str(target_db)])

    captured = capsys.readouterr()
    assert "[*] Step 1/4" in captured.err
    assert "[*] Step 4/4" in captured.err

    # stdout should contain json summary
    out_json = json.loads(captured.out)
    assert out_json["status"] == "success"
    assert out_json["schema_version"] == 1
    assert out_json["table_counts"]["users"] == 1

    # Verify database contents
    conn = sqlite3.connect(str(target_db))
    rows = conn.execute("SELECT id, name FROM users").fetchall()
    conn.close()
    assert rows == [(42, "cli_success")]


def test_cli_restore_failure_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test CLI restore exits with status code 1 on invalid backup."""
    ghost = tmp_path / "ghost.db"

    with pytest.raises(SystemExit) as exc_info:
        main(["restore", str(ghost)])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err


def test_restore_active_lock_detection(tmp_path: Path):
    """Test that active SQLite exclusive lock blocks restore unless force=True."""
    src_db = tmp_path / "src.db"
    _create_sample_db(src_db, [(1, "source")])

    backup_dir = tmp_path / "backups"
    create_backup(src_db, backup_dir, "lock_test.db")
    backup_file = backup_dir / "lock_test.db"

    target_db = tmp_path / "locked_target.db"
    _create_sample_db(target_db, [(2, "locked_target")])

    # Hold an exclusive transaction on target_db
    conn = sqlite3.connect(str(target_db))
    conn.execute("BEGIN EXCLUSIVE")

    try:
        is_offline, reason = check_target_offline(target_db)
        assert is_offline is False
        assert "currently locked" in reason

        with pytest.raises(DatabaseActiveError, match="currently locked"):
            restore_database(backup_file, target_path=target_db, force=False)
    finally:
        conn.close()


def test_restore_metadata_invalid_or_missing_sha(tmp_path: Path):
    """Test error handling when metadata is invalid JSON or lacks sha256."""
    db_file = tmp_path / "test.db"
    _create_sample_db(db_file, [(1, "one")])

    # 1. Corrupt JSON
    bad_meta = tmp_path / "test.db.meta.json"
    bad_meta.write_text("invalid json {{{", encoding="utf-8")

    with pytest.raises(BackupValidationError, match="Failed to read or parse metadata"):
        validate_backup_for_restore(db_file)

    # 2. Missing sha256
    bad_meta.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(BackupValidationError, match="missing 'sha256' field"):
        validate_backup_for_restore(db_file)


def test_rollback_restores_pre_existing_wal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test that rollback restores both target .db and -wal if -wal existed."""
    src_db = tmp_path / "src.db"
    _create_sample_db(src_db, [(10, "backup_val")])

    backup_dir = tmp_path / "backups"
    create_backup(src_db, backup_dir, "wal_rb.db")
    backup_file = backup_dir / "wal_rb.db"

    target_db = tmp_path / "target_wal.db"
    _create_sample_db(target_db, [(20, "original_val")])

    target_wal = tmp_path / "target_wal.db-wal"
    target_wal.write_bytes(b"important_wal_data_to_preserve")

    def fake_replace(src, dst):
        raise OSError("Forced failure during swap")

    monkeypatch.setattr(os, "replace", fake_replace)

    with pytest.raises(OSError, match="Forced failure during swap"):
        restore_database(backup_file, target_path=target_db, force=True)

    # Both target_db and target_wal must be restored
    assert target_db.exists()
    assert target_wal.exists()
    assert target_wal.read_bytes() == b"important_wal_data_to_preserve"
