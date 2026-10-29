from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agent_fleet import models
from agent_fleet.adapters.base import AgentResult, Detection, ParsedConfig, SourceInfo
from agent_fleet.db import (
    Base,
    create_all,
    get_engine,
    init_engine,
    run_migrations,
    session_scope,
)
from agent_fleet.services.persist import _store_config, persist_result
from agent_fleet.services.scanner import RuntimeScanResult


def _create_legacy_database(db_path: Path):
    """Create a SQLite database mimicking the schema prior to Issue #24."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")

    cur.execute(
        """
        CREATE TABLE hosts (
            id VARCHAR PRIMARY KEY,
            name VARCHAR UNIQUE,
            hostname VARCHAR,
            description VARCHAR,
            inventory_source VARCHAR DEFAULT 'sqlite',
            "group" VARCHAR,
            tags JSON,
            ssh_host VARCHAR,
            ssh_port INTEGER,
            ssh_user VARCHAR,
            proxy VARCHAR,
            runner_enabled BOOLEAN DEFAULT 0,
            runner_max_concurrency INTEGER DEFAULT 2,
            runner_labels JSON,
            target_repos JSON,
            enabled BOOLEAN DEFAULT 1,
            created_at DATETIME,
            updated_at DATETIME
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE runtimes (
            id VARCHAR PRIMARY KEY,
            host_id VARCHAR REFERENCES hosts(id) ON DELETE CASCADE,
            key VARCHAR,
            kind VARCHAR DEFAULT 'unknown',
            label VARCHAR DEFAULT '',
            transport VARCHAR DEFAULT 'ssh',
            ssh_alias VARCHAR,
            ssh_host VARCHAR,
            ssh_port INTEGER,
            collect_user VARCHAR,
            wsl_distro VARCHAR,
            wsl_user VARCHAR,
            enabled BOOLEAN DEFAULT 1,
            os_name VARCHAR,
            arch VARCHAR,
            home_dir VARCHAR,
            reachable BOOLEAN,
            last_seen_at DATETIME,
            last_success_at DATETIME,
            last_scan_at DATETIME,
            last_error TEXT,
            last_snapshot_id VARCHAR,
            CONSTRAINT uq_runtime_host_key UNIQUE (host_id, key)
        )
        """
    )

    # Legacy: config_id has NO foreign key to agent_configurations
    cur.execute(
        """
        CREATE TABLE agent_installations (
            id VARCHAR PRIMARY KEY,
            runtime_id VARCHAR REFERENCES runtimes(id) ON DELETE CASCADE,
            agent_id VARCHAR,
            status VARCHAR DEFAULT 'not_scanned',
            detected BOOLEAN DEFAULT 0,
            version VARCHAR,
            version_raw VARCHAR,
            exec_path VARCHAR,
            install_method VARCHAR,
            config_dir VARCHAR,
            detected_at DATETIME,
            error TEXT,
            config_status VARCHAR DEFAULT 'not_scanned',
            config_id VARCHAR,
            CONSTRAINT uq_runtime_agent UNIQUE (runtime_id, agent_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE config_sources (
            id VARCHAR PRIMARY KEY,
            installation_id VARCHAR REFERENCES agent_installations(id) ON DELETE CASCADE,
            scope VARCHAR DEFAULT 'user',
            path VARCHAR,
            format VARCHAR,
            kind VARCHAR DEFAULT 'config',
            present BOOLEAN DEFAULT 0,
            readable BOOLEAN DEFAULT 0,
            size_bytes INTEGER,
            status VARCHAR DEFAULT 'unknown',
            error TEXT,
            captured_at DATETIME,
            CONSTRAINT uq_source_path UNIQUE (installation_id, path)
        )
        """
    )

    # Legacy: installation_id has index but NO UNIQUE constraint
    cur.execute(
        """
        CREATE TABLE agent_configurations (
            id VARCHAR PRIMARY KEY,
            installation_id VARCHAR REFERENCES agent_installations(id) ON DELETE CASCADE,
            resolution VARCHAR DEFAULT 'declared',
            parse_status VARCHAR DEFAULT 'empty',
            model VARCHAR,
            model_provider VARCHAR,
            base_url VARCHAR,
            base_url_masked BOOLEAN DEFAULT 0,
            mcp_servers JSON,
            skills JSON,
            settings JSON,
            quota JSON,
            unsupported_fields JSON,
            sanitized JSON,
            warnings JSON,
            collected_at DATETIME
        )
        """
    )
    cur.execute("CREATE INDEX ix_agent_configurations_installation_id ON agent_configurations (installation_id);")

    # Insert historical data
    now_dt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "INSERT INTO hosts (id, name, created_at, updated_at) VALUES ('h-1', 'node-alpha', ?, ?)",
        (now_dt, now_dt),
    )
    cur.execute(
        "INSERT INTO runtimes (id, host_id, key, kind) VALUES ('rt-1', 'h-1', 'native', 'linux')"
    )

    # Installation 1: has duplicate configurations (c-old and c-new)
    cur.execute(
        "INSERT INTO agent_installations (id, runtime_id, agent_id, status, config_id) VALUES ('inst-1', 'rt-1', 'codex', 'installed', 'cfg-old')"
    )
    cur.execute(
        """
        INSERT INTO agent_configurations (id, installation_id, model, collected_at)
        VALUES ('cfg-old', 'inst-1', 'gpt-3.5', '2026-01-01 10:00:00')
        """
    )
    cur.execute(
        """
        INSERT INTO agent_configurations (id, installation_id, model, collected_at)
        VALUES ('cfg-new', 'inst-1', 'gpt-4o', '2026-01-02 12:00:00')
        """
    )

    # Installation 2: has dangling config_id that references non-existent config
    cur.execute(
        "INSERT INTO agent_installations (id, runtime_id, agent_id, status, config_id) VALUES ('inst-2', 'rt-1', 'claude', 'installed', 'cfg-nonexistent')"
    )

    # Installation 3: config_id is NULL
    cur.execute(
        "INSERT INTO agent_installations (id, runtime_id, agent_id, status, config_id) VALUES ('inst-3', 'rt-1', 'opencode', 'installed', NULL)"
    )

    con.commit()
    con.close()


def test_fresh_db_schema_integrity_and_constraints(tmp_path: Path):
    """Empty database initialization creates expected schema, unique and foreign key constraints."""
    db_file = tmp_path / "fresh.db"
    engine = init_engine(db_file)
    create_all()

    # Verify migration metadata
    with engine.connect() as conn:
        applied = conn.exec_driver_sql("SELECT version, name FROM schema_migrations").fetchall()
        assert len(applied) == 1
        assert applied[0][0] == 1
        user_ver = conn.exec_driver_sql("PRAGMA user_version").scalar()
        assert user_ver == 1

    # Verify data insertion and constraints
    with session_scope() as s:
        host = models.Host(name="node-beta")
        s.add(host)
        s.flush()
        rt = models.Runtime(host_id=host.id, key="native")
        s.add(rt)
        s.flush()
        inst = models.AgentInstallation(runtime_id=rt.id, agent_id="codex")
        s.add(inst)
        s.flush()
        cfg = models.AgentConfiguration(installation_id=inst.id, model="gpt-4o")
        s.add(cfg)
        s.flush()
        inst.config_id = cfg.id

    # a. Unique constraint violation on agent_configurations.installation_id
    with pytest.raises(IntegrityError):
        with session_scope() as s:
            dup_cfg = models.AgentConfiguration(installation_id=inst.id, model="another-model")
            s.add(dup_cfg)

    # b. Foreign key violation on agent_installations.config_id
    with pytest.raises(IntegrityError):
        with session_scope() as s:
            bad_inst = models.AgentInstallation(
                runtime_id=rt.id, agent_id="claude", config_id="invalid-config-id"
            )
            s.add(bad_inst)

    # c. Cascade delete from installation to configuration
    with session_scope() as s:
        i = s.get(models.AgentInstallation, inst.id)
        s.delete(i)

    with session_scope() as s:
        remaining_cfg = s.execute(
            select(models.AgentConfiguration).where(models.AgentConfiguration.id == cfg.id)
        ).scalar_one_or_none()
        assert remaining_cfg is None


def test_legacy_db_migration_deduplication_and_constraints(tmp_path: Path):
    """Smooth upgrade from legacy schema with duplicate configs and missing constraints."""
    db_file = tmp_path / "legacy.db"
    _create_legacy_database(db_file)

    engine = init_engine(db_file)
    create_all()

    # 1. Verify duplicate configs deduplicated: latest collected_at ('cfg-new') survived
    with session_scope() as s:
        configs = s.execute(
            select(models.AgentConfiguration).where(models.AgentConfiguration.installation_id == "inst-1")
        ).scalars().all()
        assert len(configs) == 1
        assert configs[0].id == "cfg-new"
        assert configs[0].model == "gpt-4o"

        # 2. Verify inst-1's config_id re-pointed to surviving 'cfg-new'
        inst1 = s.get(models.AgentInstallation, "inst-1")
        assert inst1.config_id == "cfg-new"

        # 3. Verify inst-2's dangling config_id was set to NULL
        inst2 = s.get(models.AgentInstallation, "inst-2")
        assert inst2.config_id is None

        # 4. Verify inst-3's NULL config_id remains NULL
        inst3 = s.get(models.AgentInstallation, "inst-3")
        assert inst3.config_id is None

    # 5. Verify unique constraint is active on migrated database
    with pytest.raises(IntegrityError):
        with session_scope() as s:
            dup_cfg = models.AgentConfiguration(installation_id="inst-1", model="conflict")
            s.add(dup_cfg)

    # 6. Verify foreign key constraint is active on migrated database
    with pytest.raises(IntegrityError):
        with session_scope() as s:
            bad_inst = models.AgentInstallation(
                runtime_id="rt-1", agent_id="new-agent", config_id="dangling-cfg"
            )
            s.add(bad_inst)


def test_migration_idempotence(tmp_path: Path):
    """Calling create_all and run_migrations repeatedly is safe and does not modify data."""
    db_file = tmp_path / "idempotence.db"
    _create_legacy_database(db_file)

    engine = init_engine(db_file)
    create_all()

    # Read data snapshot
    with session_scope() as s:
        inst_count_1 = len(s.execute(select(models.AgentInstallation)).scalars().all())
        cfg_count_1 = len(s.execute(select(models.AgentConfiguration)).scalars().all())

    # Call create_all and run_migrations again multiple times
    create_all()
    run_migrations(engine)
    create_all()

    with session_scope() as s:
        inst_count_2 = len(s.execute(select(models.AgentInstallation)).scalars().all())
        cfg_count_2 = len(s.execute(select(models.AgentConfiguration)).scalars().all())
        assert inst_count_1 == inst_count_2
        assert cfg_count_1 == cfg_count_2

    with engine.connect() as conn:
        applied = conn.exec_driver_sql("SELECT version, name FROM schema_migrations").fetchall()
        assert len(applied) == 1
        assert applied[0][0] == 1


def test_persist_store_config_with_new_constraints(tmp_path: Path):
    """persist._store_config operates safely under the unique and foreign key constraints."""
    db_file = tmp_path / "persist.db"
    init_engine(db_file)
    create_all()

    with session_scope() as s:
        h = models.Host(name="node-gamma")
        s.add(h)
        s.flush()
        rt = models.Runtime(host_id=h.id, key="native")
        s.add(rt)
        s.flush()
        inst = models.AgentInstallation(runtime_id=rt.id, agent_id="codex")
        s.add(inst)
        s.flush()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # 1. Initial config store
        cfg_res_1 = ParsedConfig(
            resolution="declared",
            parse_status="parsed",
            model="gpt-4o",
            model_provider="openai",
            base_url="https://api.example.com",
            sanitized={"model": "gpt-4o"},
        )
        stored_1 = _store_config(s, inst, cfg_res_1, now)
        inst.config_id = stored_1.id
        inst.config_status = stored_1.parse_status
        s.flush()

        first_id = stored_1.id
        assert stored_1.model == "gpt-4o"

        # 2. Subsequent config store: in-place update without unique constraint violation
        cfg_res_2 = ParsedConfig(
            resolution="effective",
            parse_status="parsed",
            model="gpt-4o-mini",
            model_provider="openai",
            base_url="https://api.example.com",
            sanitized={"model": "gpt-4o-mini"},
        )
        stored_2 = _store_config(s, inst, cfg_res_2, now)
        inst.config_id = stored_2.id
        inst.config_status = stored_2.parse_status
        s.flush()

        assert stored_2.id == first_id  # preserved in-place
        assert stored_2.model == "gpt-4o-mini"
        assert stored_2.resolution == "effective"

        # Verify only 1 config exists for this installation
        total_cfgs = s.execute(
            select(models.AgentConfiguration).where(models.AgentConfiguration.installation_id == inst.id)
        ).scalars().all()
        assert len(total_cfgs) == 1

        # 3. Empty/unusable scan result: keeps previous config
        cfg_res_empty = ParsedConfig(
            resolution="declared",
            parse_status="empty",
        )
        stored_empty = _store_config(s, inst, cfg_res_empty, now)
        assert stored_empty is not None
        assert stored_empty.id == first_id
        assert stored_empty.model == "gpt-4o-mini"


def test_persist_result_end_to_end_flow(tmp_path: Path):
    """End-to-end persist_result updates config and config_id safely across multiple scan runs."""
    db_file = tmp_path / "persist_e2e.db"
    init_engine(db_file)
    create_all()

    with session_scope() as s:
        h = models.Host(name="node-delta")
        s.add(h)
        s.flush()
        rt = models.Runtime(host_id=h.id, key="native")
        s.add(rt)
        s.flush()
        rt_id = rt.id

    class FakeSettings:
        pass

    # First scan
    scan_1 = RuntimeScanResult(
        runtime_id=rt_id,
        ok=True,
        kind="linux",
        os_name="Linux",
        arch="x86_64",
        agents=[
            AgentResult(
                agent_id="codex",
                detection=Detection(detected=True, status="installed", version="1.0.0"),
                sources=[
                    SourceInfo(scope="user", path="/etc/cfg", status="ok", size=10, content="{}")
                ],
                config=ParsedConfig(
                    parse_status="parsed",
                    resolution="declared",
                    model="model-v1",
                ),
            )
        ],
    )
    res_1 = persist_result(rt_id, scan_1, "run-1", FakeSettings())
    assert res_1["ok"] is True
    assert res_1["agents_found"] == 1

    with session_scope() as s:
        inst = s.execute(
            select(models.AgentInstallation).where(
                models.AgentInstallation.runtime_id == rt_id,
                models.AgentInstallation.agent_id == "codex",
            )
        ).scalar_one()
        assert inst.config_id is not None
        first_cfg_id = inst.config_id
        assert inst.config.model == "model-v1"
        assert len(inst.sources) == 1

    # Second scan with updated config
    scan_2 = RuntimeScanResult(
        runtime_id=rt_id,
        ok=True,
        kind="linux",
        os_name="Linux",
        arch="x86_64",
        agents=[
            AgentResult(
                agent_id="codex",
                detection=Detection(detected=True, status="installed", version="1.1.0"),
                sources=[
                    SourceInfo(scope="user", path="/etc/cfg", status="ok", size=12, content="{}")
                ],
                config=ParsedConfig(
                    parse_status="parsed",
                    resolution="effective",
                    model="model-v2",
                ),
            )
        ],
    )
    res_2 = persist_result(rt_id, scan_2, "run-2", FakeSettings())
    assert res_2["ok"] is True

    with session_scope() as s:
        inst = s.execute(
            select(models.AgentInstallation).where(
                models.AgentInstallation.runtime_id == rt_id,
                models.AgentInstallation.agent_id == "codex",
            )
        ).scalar_one()
        # Ensure updated in-place
        assert inst.config_id == first_cfg_id
        assert inst.config.model == "model-v2"
        assert inst.config.resolution == "effective"


def test_legacy_migration_with_config_sources_and_tie_breaking(tmp_path: Path):
    """Test tie-breaking on identical collected_at and preservation of config_sources."""
    db_file = tmp_path / "tie_break.db"
    con = sqlite3.connect(db_file)
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("CREATE TABLE hosts (id VARCHAR PRIMARY KEY, name VARCHAR UNIQUE)")
    con.execute("CREATE TABLE runtimes (id VARCHAR PRIMARY KEY, host_id VARCHAR REFERENCES hosts(id) ON DELETE CASCADE, key VARCHAR)")
    con.execute("""
        CREATE TABLE agent_installations (
            id VARCHAR PRIMARY KEY,
            runtime_id VARCHAR REFERENCES runtimes(id) ON DELETE CASCADE,
            agent_id VARCHAR,
            status VARCHAR DEFAULT 'not_scanned',
            detected BOOLEAN DEFAULT 0,
            version VARCHAR,
            version_raw VARCHAR,
            exec_path VARCHAR,
            install_method VARCHAR,
            config_dir VARCHAR,
            detected_at DATETIME,
            error TEXT,
            config_status VARCHAR DEFAULT 'not_scanned',
            config_id VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE config_sources (
            id VARCHAR PRIMARY KEY,
            installation_id VARCHAR REFERENCES agent_installations(id) ON DELETE CASCADE,
            scope VARCHAR DEFAULT 'user',
            path VARCHAR,
            format VARCHAR,
            kind VARCHAR DEFAULT 'config',
            present BOOLEAN DEFAULT 0,
            readable BOOLEAN DEFAULT 0,
            size_bytes INTEGER,
            status VARCHAR DEFAULT 'unknown',
            error TEXT,
            captured_at DATETIME
        )
    """)
    con.execute("""
        CREATE TABLE agent_configurations (
            id VARCHAR PRIMARY KEY,
            installation_id VARCHAR REFERENCES agent_installations(id) ON DELETE CASCADE,
            resolution VARCHAR DEFAULT 'declared',
            parse_status VARCHAR DEFAULT 'empty',
            model VARCHAR,
            collected_at DATETIME
        )
    """)
    con.execute("INSERT INTO hosts VALUES ('h-1', 'node-omega')")
    con.execute("INSERT INTO runtimes VALUES ('rt-1', 'h-1', 'native')")
    con.execute("INSERT INTO agent_installations (id, runtime_id, agent_id, config_id) VALUES ('inst-1', 'rt-1', 'codex', 'cfg-a')")
    con.execute("INSERT INTO config_sources (id, installation_id, path) VALUES ('cs-1', 'inst-1', '/path/a')")
    con.execute("INSERT INTO config_sources (id, installation_id, path) VALUES ('cs-2', 'inst-1', '/path/b')")

    # Identical collected_at, different IDs: tie-breaking by id desc selects 'cfg-z' over 'cfg-a'
    con.execute("INSERT INTO agent_configurations (id, installation_id, model, collected_at) VALUES ('cfg-a', 'inst-1', 'm-a', '2026-01-01 12:00:00')")
    con.execute("INSERT INTO agent_configurations (id, installation_id, model, collected_at) VALUES ('cfg-z', 'inst-1', 'm-z', '2026-01-01 12:00:00')")
    con.commit()
    con.close()

    init_engine(db_file)
    create_all()

    with session_scope() as s:
        # Check surviving config is cfg-z
        cfgs = s.execute(select(models.AgentConfiguration)).scalars().all()
        assert len(cfgs) == 1
        assert cfgs[0].id == "cfg-z"
        assert cfgs[0].model == "m-z"

        # Check installation config_id updated to cfg-z
        inst = s.get(models.AgentInstallation, "inst-1")
        assert inst.config_id == "cfg-z"

        # Check config sources preserved intact
        sources = s.execute(select(models.ConfigSource)).scalars().all()
        assert len(sources) == 2

