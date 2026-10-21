"""Paseo native CLI schema, failure isolation and safe persistence; no remotes."""
import asyncio
import json

import pytest
from sqlalchemy import delete

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.paseo_models import PaseoObservation
from agent_fleet.remotes.executor import ExecResult
from agent_fleet.services import paseo_service as svc
from agent_fleet.services.scanner import RuntimeRef
from test_scan import _make_runtime


class Runner:
    def __init__(self, status=None, sessions=None, detect=None):
        self.calls = []
        self.status = status or ExecResult(stdout=json.dumps({"localDaemon": "running", "connectedDaemon": "reachable", "daemonVersion": "0.12.0", "listen": "127.0.0.1:6767", "relay": {"token": "never-store"}}))
        self.sessions = sessions or ExecResult(stdout=json.dumps([{"id": "paseo-uuid", "name": "Project", "provider": "codex/gpt-5", "cwd": "~/project", "status": "running", "password": "never-store"}]))
        self.detect = detect or ExecResult(stdout="FLEET_PASEO_FOUND\n")

    async def run_script(self, script, timeout):
        self.calls.append(script)
        if "FLEET_PASEO_FOUND" in script:
            return self.detect
        if "--version" in script:
            return ExecResult(stdout="0.12.0\n")
        if "status" in script:
            return self.status
        return self.sessions


def observe(monkeypatch, settings, runner, transport="ssh"):
    monkeypatch.setattr(svc, "build_runner", lambda *_: runner)
    return asyncio.run(svc.observe(RuntimeRef(id="rt", transport=transport), settings))


def test_paseo_native_schema_allowlist(fleet_db, monkeypatch):
    runner = Runner()
    result = observe(monkeypatch, fleet_db, runner)
    assert result["status"] == "running"
    assert result["facts"]["endpoint"] == "127.0.0.1:6767"
    assert "never-store" not in json.dumps(result)
    item = result["sessions"][0]
    assert item["provider"] == "codex"
    assert item["external_id"] == "paseo-uuid"
    assert item["provider_session_id"] is None
    assert item["source"] == "paseo"
    assert "ls -a --json" in runner.calls[-1]
    assert not any("daemon start" in call for call in runner.calls)


@pytest.mark.parametrize("payload,status", [
    ({"localDaemon": "stopped", "connectedDaemon": "not_probed"}, "stopped"),
    ({"localDaemon": "not_ready", "connectedDaemon": "not_probed"}, "not_ready"),
    ({"connectedDaemon": "auth_required", "note": "password=secret-1234"}, "authentication_failed"),
    ({"connectedDaemon": "unreachable"}, "connection_failed"),
    ({"novel": True}, "unsupported"),
    ([], "unsupported"),
])
def test_paseo_statuses(fleet_db, monkeypatch, payload, status):
    result = observe(monkeypatch, fleet_db, Runner(status=ExecResult(stdout=json.dumps(payload))))
    assert result["status"] == status
    assert "secret-1234" not in json.dumps(result)


@pytest.mark.parametrize("failure,status", [
    (ExecResult(exit_code=1, stderr='{"error":{"code":"AUTH_FAILED","message":"password=secret-1234"}}'), "authentication_failed"),
    (ExecResult(exit_code=1, stderr="unknown option --json"), "unsupported"),
    (ExecResult(timed_out=True), "timeout"),
    (ExecResult(exit_code=255, stderr="connection refused"), "connection_failed"),
])
def test_paseo_safe_failure(fleet_db, monkeypatch, failure, status):
    result = observe(monkeypatch, fleet_db, Runner(status=failure))
    assert result["status"] == status
    assert "secret-1234" not in json.dumps(result)


def test_paseo_missing_and_windows(fleet_db, monkeypatch):
    runner = Runner(detect=ExecResult(stdout="FLEET_PASEO_MISSING\n"))
    assert observe(monkeypatch, fleet_db, runner)["facts"] == {"installed": False}
    assert len(runner.calls) == 1
    runner = Runner()
    assert observe(monkeypatch, fleet_db, runner, "windows")["status"] == "running"
    assert "Get-Command paseo" in runner.calls[0]
    assert "& paseo 'daemon' 'status' '--json'" in runner.calls[2]


def test_paseo_session_failure_keeps_service_observation(fleet_db, monkeypatch):
    result = observe(monkeypatch, fleet_db, Runner(sessions=ExecResult(stdout='{"changed": true}')))
    assert result["status"] == "running"
    assert result["sessions_status"] == "unsupported"
    assert "sessions" not in result


def test_paseo_persistence_keeps_good_sessions_and_facts(fleet_db):
    rid = _make_runtime(fleet_db)
    svc._persist(rid, {"status": "running", "facts": {"installed": True, "version": "0.12.0"}, "sessions": [{"external_id": "one"}], "sessions_status": "ok"})
    svc._persist(rid, {"status": "authentication_failed"})
    item = svc.list_observations()["items"][0]
    assert item["stale"] and item["sessions_stale"]
    assert item["version"] == "0.12.0"
    assert item["sessions"] == [{"external_id": "one"}]
    assert item["last_success_at"]
    with session_scope() as s:
        s.execute(delete(models.Runtime).where(models.Runtime.id == rid))
    svc._persist(rid, {"status": "timeout"})
    with session_scope() as s:
        assert s.get(PaseoObservation, rid) is None


def test_paseo_session_text_masked():
    result = svc._sessions([{"id": "one", "name": "password=secret-1234", "cwd": "api_key=super-secret-1234", "provider": "codex", "status": "idle"}], "rt")
    assert "secret-1234" not in json.dumps(result)
    assert "MASKED" in result[0]["title"]


def test_paseo_refresh_failure_isolated_and_disabled_skipped(fleet_db, monkeypatch):
    a = _make_runtime(fleet_db, name="a")
    b = _make_runtime(fleet_db, name="b")
    disabled = _make_runtime(fleet_db, name="disabled")
    with session_scope() as s:
        s.get(models.Runtime, disabled).enabled = False
    seen = []

    async def fake(ref, _settings):
        seen.append(ref.id)
        if ref.id == a:
            raise RuntimeError("password=not-to-echo")
        return {"status": "stopped", "facts": {"installed": True}}
    monkeypatch.setattr(svc, "observe", fake)
    result = asyncio.run(svc.refresh(None, fleet_db))
    assert set(seen) == {a, b}
    assert "not-to-echo" not in json.dumps(result)
    states = {item["runtime_id"]: item["status"] for item in result["items"]}
    assert states == {a: "internal_error", b: "stopped", disabled: "unknown"}


def test_paseo_get_never_runs_remote(fleet_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_fleet.api.paseo_routes import router
    _make_runtime(fleet_db)
    monkeypatch.setattr(svc, "build_runner", lambda *_: pytest.fail("GET ran a remote"))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        assert client.get("/api/paseo").json()["items"][0]["status"] == "unknown"
        assert client.post("/api/paseo/refresh", json={"runtime_ids": ["missing"]}).status_code == 404
        assert client.post("/api/paseo/refresh", json={"runtime_ids": []}).json() == {"items": []}


def test_paseo_stopped_retains_previously_listed_sessions(fleet_db):
    rid = _make_runtime(fleet_db)
    svc._persist(rid, {"status": "running", "facts": {"installed": True}, "sessions": [{"external_id": "one"}], "sessions_status": "ok"})
    svc._persist(rid, {"status": "stopped", "facts": {"installed": True}, "sessions_status": "unavailable"})
    item = svc.list_observations()["items"][0]
    assert item["status"] == "stopped"
    assert not item["stale"]
    assert item["sessions_stale"]
    assert item["sessions"] == [{"external_id": "one"}]


def test_paseo_write_failure_does_not_abort_other_runtime(fleet_db, monkeypatch):
    a = _make_runtime(fleet_db, name="write-a")
    b = _make_runtime(fleet_db, name="write-b")
    original = svc._persist

    async def fake(*_args):
        return {"status": "stopped", "facts": {"installed": True}}

    def write(rid, result):
        if rid == a:
            raise RuntimeError("deleted during observation")
        original(rid, result)

    monkeypatch.setattr(svc, "observe", fake)
    monkeypatch.setattr(svc, "_persist", write)
    result = asyncio.run(svc.refresh(None, fleet_db))
    assert next(row for row in result["items"] if row["runtime_id"] == b)["status"] == "stopped"
