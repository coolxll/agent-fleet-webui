"""Herdr native CLI schema, failure isolation and safe persistence."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.herdr_models import HerdrObservation
from agent_fleet.main import app
from agent_fleet.remotes.executor import ExecResult
from agent_fleet.services import herdr_service as svc
from agent_fleet.services.scanner import RuntimeRef
from test_scan import _make_runtime


class Runner:
    def __init__(self, status=None, agents=None, workspaces=None, detect=None):
        self.calls = []
        self.status = status or ExecResult(stdout=json.dumps({
            "client": {"version": "0.9.5", "channel": "stable"},
            "server": {"status": "running", "running": True, "version": "0.9.5", "socket": "/home/user/.config/herdr/herdr.sock", "protocol": 22},
            "update": {"restart_needed": False},
        }))
        self.workspaces = workspaces or ExecResult(stdout=json.dumps({
            "id": "cli:workspace:list",
            "result": {
                "type": "workspace_list",
                "workspaces": [
                    {"workspace_id": "w1", "label": "test-ws", "pane_count": 2, "tab_count": 1, "focused": True, "agent_status": "working"}
                ]
            }
        }))
        self.agents = agents or ExecResult(stdout=json.dumps({
            "id": "cli:agent:list",
            "result": {
                "type": "agent_list",
                "agents": [
                    {"agent": "codex", "agent_status": "idle", "pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1", "terminal_title": "test agent", "cwd": "/home/user/project"}
                ]
            }
        }))
        self.detect = detect or ExecResult(stdout="FLEET_HERDR_FOUND\n")

    async def run_script(self, script, timeout):
        self.calls.append(script)
        if "FLEET_HERDR_FOUND" in script:
            return self.detect
        if "--version" in script:
            return ExecResult(stdout="herdr 0.9.5\n")
        if "status" in script:
            return self.status
        if "workspace" in script:
            return self.workspaces
        return self.agents


def observe(monkeypatch, settings, runner, transport="ssh"):
    monkeypatch.setattr(svc, "build_runner", lambda *_: runner)
    return asyncio.run(svc.observe(RuntimeRef(id="rt", transport=transport), settings))


def test_herdr_native_schema_allowlist(fleet_db, monkeypatch):
    runner = Runner()
    result = observe(monkeypatch, fleet_db, runner)
    assert result["status"] == "running"
    assert result["facts"]["socket"] == "/home/user/.config/herdr/herdr.sock"
    assert result["facts"]["version"] == "0.9.5"
    assert len(result["workspaces"]) == 1
    assert result["workspaces"][0]["workspace_id"] == "w1"
    item = result["sessions"][0]
    assert item["provider"] == "codex"
    assert item["external_id"] == "w1:p1"
    assert item["source"] == "herdr"


@pytest.mark.parametrize("payload,status", [
    ({"server": {"status": "stopped", "running": False}}, "stopped"),
    ({"server": {"status": "not_ready", "running": False}}, "not_ready"),
    ({"server": "invalid"}, "unsupported"),
    ([], "unsupported"),
])
def test_herdr_statuses(fleet_db, monkeypatch, payload, status):
    result = observe(monkeypatch, fleet_db, Runner(status=ExecResult(stdout=json.dumps(payload))))
    assert result["status"] == status


def test_herdr_missing_and_windows(fleet_db, monkeypatch):
    runner = Runner(detect=ExecResult(stdout="FLEET_HERDR_MISSING\n"))
    assert observe(monkeypatch, fleet_db, runner)["facts"] == {"installed": False}
    assert len(runner.calls) == 1
    runner = Runner()
    assert observe(monkeypatch, fleet_db, runner, "windows")["status"] == "running"
    assert "Get-Command herdr" in runner.calls[0]
    assert "& herdr 'status' '--json'" in runner.calls[2]


def test_herdr_persistence_keeps_good_sessions_and_facts(fleet_db):
    rid = _make_runtime(fleet_db)
    svc._persist(rid, {
        "status": "running",
        "facts": {"installed": True, "version": "0.9.5"},
        "workspaces": [{"workspace_id": "w1"}],
        "sessions": [{"external_id": "w1:p1"}],
        "sessions_status": "ok",
    })
    svc._persist(rid, {"status": "timeout"})
    item = svc.list_observations()["items"][0]
    assert item["stale"]
    assert item["version"] == "0.9.5"
    assert item["workspaces"] == [{"workspace_id": "w1"}]
    assert item["sessions"] == [{"external_id": "w1:p1"}]
    assert item["last_success_at"]

    with session_scope() as s:
        s.execute(delete(models.Runtime).where(models.Runtime.id == rid))
    svc._persist(rid, {"status": "timeout"})
    with session_scope() as s:
        assert s.get(HerdrObservation, rid) is None


def test_daemon_routes_api(fleet_db, monkeypatch):
    client = TestClient(app)
    rid = _make_runtime(fleet_db)

    svc._persist(rid, {
        "status": "running",
        "facts": {"installed": True, "version": "0.9.5", "socket": "/sock"},
        "workspaces": [],
        "sessions": [],
        "sessions_status": "ok",
    })

    # Test GET /api/herdr
    res = client.get(f"/api/herdr?runtime_id={rid}")
    assert res.status_code == 200
    assert len(res.json()["items"]) == 1
    assert res.json()["items"][0]["runtime_id"] == rid

    # Test GET /api/daemons
    res = client.get(f"/api/daemons?runtime_id={rid}")
    assert res.status_code == 200
    data = res.json()
    assert "paseo" in data and "herdr" in data
    assert len(data["herdr"]) == 1
    assert data["herdr"][0]["runtime_id"] == rid


def test_daemon_upgrade_detection(fleet_db, monkeypatch):
    from agent_fleet.services import updates
    monkeypatch.setattr(updates, "latest_available", lambda agent_id: {
        "source": "github" if agent_id == "herdr" else "npm",
        "package": "herdrdev/herdr" if agent_id == "herdr" else "@getpaseo/cli",
        "latest_available": "1.0.0",
        "release_url": "https://example.com/release",
    })

    rid = _make_runtime(fleet_db)
    svc._persist(rid, {
        "status": "running",
        "facts": {"installed": True, "version": "0.9.5"},
        "workspaces": [],
        "sessions": [],
        "sessions_status": "ok",
    })

    obs = svc.list_observations([rid])["items"][0]
    assert obs["latest_version"] == "1.0.0"
    assert obs["upgrade_available"] is True
    assert obs["release_url"] == "https://example.com/release"
