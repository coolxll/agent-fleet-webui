"""Unit tests for agent-fleet CLI subcommands (matrix, compare, quotas, tasks, daemons)."""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from agent_fleet import cli, models
from agent_fleet.db import session_scope
from agent_fleet.herdr_models import HerdrObservation
from agent_fleet.paseo_models import PaseoObservation
from agent_fleet.task_models import FleetTask, TaskStage


def _seed_fleet():
    now = models.utcnow()
    with session_scope() as s:
        h1 = models.Host(name="mac-local", hostname="mac-local", enabled=True)
        h2 = models.Host(name="corp172-dev", hostname="corp172-dev", enabled=True)
        h3 = models.Host(name="disabled-box", hostname="disabled-box", enabled=False)
        s.add_all([h1, h2, h3])
        s.flush()

        r1 = models.Runtime(
            host_id=h1.id,
            key="native",
            kind="macos",
            transport="local",
            collect_user="lynn",
            enabled=True,
            reachable=True,
            last_scan_at=now,
            last_success_at=now,
        )
        r2 = models.Runtime(
            host_id=h2.id,
            key="native",
            kind="linux",
            transport="ssh",
            ssh_alias="corp172-dev",
            collect_user="dev",
            enabled=True,
            reachable=True,
            last_scan_at=now,
            last_success_at=now,
        )
        r3 = models.Runtime(
            host_id=h3.id,
            key="native",
            kind="linux",
            transport="ssh",
            enabled=True,
            reachable=True,
            last_scan_at=now,
            last_success_at=now,
        )
        s.add_all([r1, r2, r3])
        s.flush()

        # claude on mac-local (with URL containing query token and secret-looking warning)
        i1 = models.AgentInstallation(
            runtime_id=r1.id,
            agent_id="claude",
            status="installed",
            detected=True,
            version="2.1.10",
            exec_path="/opt/homebrew/bin/claude",
            install_method="npm",
            config_status="parsed",
        )
        # codex on mac-local (not installed)
        i2 = models.AgentInstallation(
            runtime_id=r1.id,
            agent_id="codex",
            status="not_installed",
            detected=False,
            config_status="empty",
        )
        # claude on corp172-dev (different model & version)
        i3 = models.AgentInstallation(
            runtime_id=r2.id,
            agent_id="claude",
            status="installed",
            detected=True,
            version="2.0.5",
            exec_path="/usr/local/bin/claude",
            install_method="npm",
            config_status="parsed",
        )
        # agy on corp172-dev with runtime quota
        i4 = models.AgentInstallation(
            runtime_id=r2.id,
            agent_id="agy",
            status="installed",
            detected=True,
            version="1.4.2",
            config_status="parsed",
        )
        s.add_all([i1, i2, i3, i4])
        s.flush()

        c1 = models.AgentConfiguration(
            installation_id=i1.id,
            parse_status="parsed",
            model="claude-sonnet-4-5",
            model_provider="anthropic",
            base_url="https://user:secretpass@api.anthropic.com/v1?api_key=sk-ant-1234567890abcdef",
            base_url_masked=False,
            warnings=["sk-ant-1234567890abcdef", "normal warning"],
            settings={"theme": "dark"},
        )
        c3 = models.AgentConfiguration(
            installation_id=i3.id,
            parse_status="parsed",
            model="claude-opus-4-1",
            model_provider="anthropic",
            base_url="https://proxy.example.com/v1",
            base_url_masked=False,
            warnings=[],
            settings={"theme": "light"},
        )
        c4 = models.AgentConfiguration(
            installation_id=i4.id,
            parse_status="parsed",
            model="gemini-2.5-pro",
            model_provider="google",
            quota={
                "source": "agy-swap",
                "observed_at": "2026-11-22T00:00:00Z",
                "accounts": [
                    {
                        "label": "work",
                        "email": "work@example.com",
                        "plan": "pro",
                        "min_remaining_fraction": 0.72,
                        "access_token": "ya29.super-secret-token-value",
                    }
                ],
            },
        )
        s.add_all([c1, c3, c4])
        s.flush()
        i1.config_id = c1.id
        i3.config_id = c3.id
        i4.config_id = c4.id

        # Daemon observations
        s.add(
            HerdrObservation(
                runtime_id=r1.id,
                status="running",
                checked_at=now,
                last_success_at=now,
                facts={"installed": True, "server_version": "0.5.1"},
                workspaces=[{"workspace_id": "ws-1", "label": "main"}],
                sessions=[{"external_id": "p-1", "provider": "claude", "status": "idle"}],
                sessions_status="ok",
                sessions_checked_at=now,
                sessions_success_at=now,
            )
        )
        s.add(
            PaseoObservation(
                runtime_id=r2.id,
                status="stopped",
                checked_at=now,
                last_success_at=now - timedelta(hours=2),
                error_code="stopped",
                error="Paseo daemon is stopped.",
                facts={"installed": True, "version": "0.12.0"},
                sessions=[],
                sessions_status="unavailable",
            )
        )

        # Tasks
        t1 = FleetTask(title="Audit configs", goal="Check drift", status="todo", project_path="/workspace/a")
        t2 = FleetTask(title="Ship release", goal="Tag v1", status="in_progress", project_path="/workspace/b")
        s.add_all([t1, t2])
        s.flush()
        s.add(
            TaskStage(
                task_id=t2.id,
                runtime_id=r2.id,
                host_name="corp172-dev",
                agent_id="claude",
                project_path="/workspace/b",
                kind="handoff",
                status="completed",
            )
        )

        return {"r1": r1.id, "r2": r2.id, "r3": r3.id, "t1": t1.id, "t2": t2.id}


def test_cli_matrix_filters_and_sanitization(fleet_db, capsys):
    _seed_fleet()

    cli.main(["matrix"])
    data = json.loads(capsys.readouterr().out)
    hosts = [r["host"] for r in data["runtimes"]]
    assert hosts == ["corp172-dev", "mac-local"]
    assert "disabled-box" not in hosts

    mac = next(r for r in data["runtimes"] if r["host"] == "mac-local")
    claude = mac["agents"]["claude"]
    assert claude["installed"] is True
    assert claude["status"] == "installed"
    assert claude["version"] == "2.1.10"
    assert claude["provider"] == "anthropic"
    assert claude["model"] == "claude-sonnet-4-5"
    assert claude["base_url"] == "https://api.anthropic.com/v1"
    assert claude["base_url_masked"] is True
    assert "secretpass" not in json.dumps(data)
    assert "sk-ant-1234567890abcdef" not in json.dumps(data)
    assert "normal warning" in claude["warnings"]
    assert mac["agents"]["codex"]["installed"] is False
    assert mac["agents"]["codex"]["status"] == "not_installed"

    # Filter by --agent and --host
    cli.main(["matrix", "--agent", "claude", "--host", "corp172-dev"])
    filtered = json.loads(capsys.readouterr().out)
    assert len(filtered["runtimes"]) == 1
    assert filtered["runtimes"][0]["host"] == "corp172-dev"
    assert list(filtered["runtimes"][0]["agents"].keys()) == ["claude"]

    # Unknown agent exits 2
    with pytest.raises(SystemExit) as exc:
        cli.main(["matrix", "--agent", "nonexistent-agent"])
    assert exc.value.code == 2


def test_cli_compare_diff_and_errors(fleet_db, capsys):
    _seed_fleet()

    cli.main(["compare", "mac-local", "corp172-dev", "--agent", "claude"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["same_agent"] is True
    assert data["identical"] is False
    assert data["a"]["runtime"]["host"] == "mac-local"
    assert data["b"]["runtime"]["host"] == "corp172-dev"
    paths = {c["path"]: c for c in data["changes"]}
    assert "version" in paths
    assert paths["version"]["a"] == "2.1.10"
    assert paths["version"]["b"] == "2.0.5"
    assert "model" in paths
    assert paths["base_url"]["a"] == "https://api.anthropic.com/v1"
    assert "secretpass" not in out
    assert "sk-ant-1234567890abcdef" not in out

    # Host/key selector works too
    cli.main(["compare", "mac-local/native", "mac-local", "--agent", "claude"])
    same = json.loads(capsys.readouterr().out)
    assert same["identical"] is True
    assert same["changes"] == []

    # Missing host exits 2
    with pytest.raises(SystemExit) as exc:
        cli.main(["compare", "unknown-host", "mac-local", "--agent", "claude"])
    assert exc.value.code == 2

    # Missing agent on host exits 2
    with pytest.raises(SystemExit) as exc:
        cli.main(["compare", "mac-local", "corp172-dev", "--agent", "agy"])
    assert exc.value.code == 2


def test_cli_quotas_sanitized(fleet_db, tmp_path, monkeypatch, capsys):
    _seed_fleet()
    # Point magpie config dir to an empty temp dir so test is deterministic and offline
    monkeypatch.setenv("AGENT_FLEET_MAGPIE_CONFIG_DIR", str(tmp_path / "no-magpie"))
    from agent_fleet.settings import get_settings

    get_settings.cache_clear()

    # Seed an imported quota account with a raw credential in quota_accounts.json
    store_file = fleet_db.db_path.parent / "quota_accounts.json"
    store_file.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": [
                    {
                        "id": "qa-1",
                        "provider": "codex",
                        "label": "personal-codex",
                        "email": "me@example.com",
                        "plan": "plus",
                        "status": "ok",
                        "credential_source": "manual",
                        "credential": {
                            "access_token": "eyJhbGciOiJSUzI1NiJ9.TOP_SECRET_TOKEN_PAYLOAD.sig",
                            "refresh_token": "rt_super_secret_refresh",
                        },
                        "quota": {
                            "source": "openai",
                            "observed_at": "2026-11-22T01:00:00Z",
                            "accounts": [{"min_remaining_fraction": 0.45}],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    cli.main(["quotas"])
    raw_out = capsys.readouterr().out
    data = json.loads(raw_out)

    # Secrets MUST NEVER appear in stdout
    assert "TOP_SECRET_TOKEN_PAYLOAD" not in raw_out
    assert "rt_super_secret_refresh" not in raw_out
    assert "ya29.super-secret-token-value" not in raw_out

    assert len(data["runtime_quotas"]) == 1
    assert data["runtime_quotas"][0]["agent"] == "agy"
    assert data["runtime_quotas"][0]["min_remaining_fraction"] == 0.72

    assert len(data["imported_accounts"]) == 1
    assert data["imported_accounts"][0]["provider"] == "codex"
    assert data["imported_accounts"][0]["quota_summary"]["min_remaining_fraction"] == 0.45
    assert "credential" not in data["imported_accounts"][0]

    assert data["sources"]["magpie"]["available"] is False


def test_cli_tasks_list_and_create(fleet_db, capsys):
    _seed_fleet()

    cli.main(["tasks", "list"])
    all_tasks = json.loads(capsys.readouterr().out)
    assert all_tasks["count"] == 2

    cli.main(["tasks", "list", "--status", "in_progress"])
    in_prog = json.loads(capsys.readouterr().out)
    assert in_prog["count"] == 1
    assert in_prog["tasks"][0]["title"] == "Ship release"
    assert in_prog["tasks"][0]["target_host"] == "corp172-dev"
    assert in_prog["tasks"][0]["agent_id"] == "claude"

    cli.main(
        [
            "tasks",
            "create",
            "--title",
            "Verify CLI integration",
            "--description",
            "Add JSON CLI and SKILL.md",
            "--target-host",
            "corp172-dev",
            "--agent",
            "codex",
            "--project-path",
            "/workspace/agent-fleet-webui",
        ]
    )
    created = json.loads(capsys.readouterr().out)
    assert created["title"] == "Verify CLI integration"
    assert created["status"] == "todo"
    assert created["target_host"] == "corp172-dev"
    assert created["agent"] == "codex"
    assert "target_host: corp172-dev" in created["goal"]
    assert "agent: codex" in created["goal"]

    # Confirm persisted
    cli.main(["tasks", "list", "--status", "todo"])
    todos = json.loads(capsys.readouterr().out)
    assert any(t["id"] == created["id"] for t in todos["tasks"])

    # Empty title rejected
    with pytest.raises(SystemExit) as exc:
        cli.main(["tasks", "create", "--title", "   "])
    assert exc.value.code == 2


def test_cli_daemons_cached_no_network(fleet_db, monkeypatch, capsys):
    _seed_fleet()

    # Guard against any outbound network calls in read-only daemons command
    import urllib.request

    def _forbid_urlopen(*_a, **_kw):
        raise AssertionError("network call must not happen during read-only CLI command")

    monkeypatch.setattr(urllib.request, "urlopen", _forbid_urlopen)

    cli.main(["daemons"])
    data = json.loads(capsys.readouterr().out)
    assert len(data["herdr"]) == 2
    assert len(data["paseo"]) == 2

    mac_herdr = next(x for x in data["herdr"] if x["host"] == "mac-local")
    assert mac_herdr["status"] == "running"
    assert mac_herdr["version"] == "0.5.1"
    assert mac_herdr["workspace_count"] == 1
    assert mac_herdr["session_count"] == 1
    assert mac_herdr["stale"] is False

    corp_paseo = next(x for x in data["paseo"] if x["host"] == "corp172-dev")
    assert corp_paseo["status"] == "stopped"
    assert corp_paseo["stale"] is True

    # Filter by --host
    cli.main(["daemons", "--host", "mac-local"])
    filtered = json.loads(capsys.readouterr().out)
    assert [x["host"] for x in filtered["herdr"]] == ["mac-local"]
    assert [x["host"] for x in filtered["paseo"]] == ["mac-local"]


def test_wrapper_script_help():
    repo_root = Path(__file__).resolve().parents[1]
    wrapper = repo_root / "scripts" / "agent-fleet"
    assert wrapper.is_file()
    res = subprocess.run(
        [str(wrapper), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert res.returncode == 0
    for cmd in ("matrix", "compare", "quotas", "tasks", "daemons"):
        assert cmd in res.stdout
