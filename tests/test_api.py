"""API surface tests with an in-memory-ish SQLite file and fake runners."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope

from test_scan import FakeRunner, _codex_stream, _make_runtime


@pytest.fixture()
def client(fleet_db, monkeypatch):
    monkeypatch.setenv("AGENT_FLEET_INCLUDE_LOCAL_RUNTIME", "false")
    from agent_fleet.main import app

    with TestClient(app) as c:
        yield c


def _seed(fleet_db, monkeypatch, stream=_codex_stream(), count=1):
    ids = []
    for i in range(count):
        ids.append(_make_runtime(fleet_db, name="api-box-" + str(i)))
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner", lambda rt, s: FakeRunner(stream)
    )
    return ids


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


def test_agents_endpoint(client):
    data = client.get("/api/agents").json()
    ids = [a["id"] for a in data["agents"]]
    assert {"codex", "claude", "opencode", "pi", "hermes"} <= set(ids)


def test_overview_and_matrix_after_scan(client, fleet_db, monkeypatch):
    ids = _seed(fleet_db, monkeypatch, count=2)
    for rid in ids:
        _scan_one(rid, fleet_db)

    ov = client.get("/api/overview").json()
    assert ov["totals"]["runtimes"] == 2
    assert ov["totals"]["online"] == 2
    assert ov["totals"]["installations"] == 2
    codex = next(a for a in ov["agents"] if a["id"] == "codex")
    assert codex["installations"] == 2

    m = client.get("/api/matrix").json()
    assert len(m["columns"]) == 2
    for col in m["columns"]:
        assert col["cells"]["codex"]["state"] == "installed"
        assert col["cells"]["claude"]["state"] == "not_installed"
        assert col["cells"]["codex"]["installation_id"]


def test_inspector_detail_is_sanitized(client, fleet_db, monkeypatch):
    rid = _seed(fleet_db, monkeypatch)[0]
    _scan_one(rid, fleet_db)

    insts = client.get("/api/installations?agent_id=codex").json()["installations"]
    assert len(insts) == 1
    detail = client.get("/api/installations/" + insts[0]["id"]).json()
    assert detail["status"] == "installed"
    assert detail["config"]["model"] == "gpt-5-fixture"
    src = next(s for s in detail["sources"] if s["path"] == "~/.codex/config.toml")
    assert src["status"] == "ok"
    assert src["readable"] is True
    blob = str(detail)
    assert "sk-" not in blob


def test_installations_filter(client, fleet_db, monkeypatch):
    _seed(fleet_db, monkeypatch, count=2)
    for rid in [r for r in _all_runtimes(fleet_db)]:
        _scan_one(rid, fleet_db)
    only = client.get("/api/installations?detected_only=true").json()["installations"]
    assert len(only) == 2
    assert all(i["detected"] for i in only)


def test_matrix_shows_scan_failure_without_losing_data(client, fleet_db, monkeypatch):
    rid = _seed(fleet_db, monkeypatch)[0]
    _scan_one(rid, fleet_db)
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner",
        lambda rt, s: FakeRunner(error="ssh: connect to host api-box-0 port 22: Connection refused"),
    )
    _scan_one(rid, fleet_db)

    m = client.get("/api/matrix").json()
    cell = m["columns"][0]["cells"]["codex"]
    assert cell["state"] == "scan_failed"
    assert "Connection refused" in cell["error"]
    # the last known version is still shown
    assert cell["version"] == "1.2.3"
    assert cell["last_success"]


def test_scan_endpoint_rejects_empty_fleet(client, fleet_db):
    res = client.post("/api/scan", json={"all": True})
    assert res.status_code == 400


def test_disabled_host_is_hidden_from_fleet_views(client, fleet_db, monkeypatch):
    """An excluded host disappears from overview/matrix but keeps its data."""
    rid = _seed(fleet_db, monkeypatch)[0]
    _scan_one(rid, fleet_db)
    assert client.get('/api/overview').json()['totals']['runtimes'] == 1

    with session_scope() as s:
        host = s.get(models.Runtime, rid).host
        host_id = host.id

    res = client.post('/api/hosts/' + host_id + '/enabled', json={'enabled': False})
    assert res.status_code == 200
    assert res.json()['enabled'] is False

    ov = client.get('/api/overview').json()
    assert ov['totals']['runtimes'] == 0
    assert len(client.get('/api/matrix').json()['columns']) == 0
    # the data itself is untouched and still reachable directly
    inst = client.get('/api/installations?runtime_id=' + rid).json()['installations']
    assert len(inst) == 5

    client.post('/api/hosts/' + host_id + '/enabled', json={'enabled': True})
    assert client.get('/api/overview').json()['totals']['runtimes'] == 1


def test_delete_runtime_removes_everything(client, fleet_db, monkeypatch):
    rid = _seed(fleet_db, monkeypatch)[0]
    _scan_one(rid, fleet_db)
    assert client.get('/api/installations?runtime_id=' + rid).json()['installations']

    res = client.delete('/api/runtimes/' + rid)
    assert res.status_code == 200
    assert res.json()['ok'] is True

    assert client.get('/api/installations?runtime_id=' + rid).json()['installations'] == []
    assert client.get('/api/runtimes').json()['runtimes'] == []
    assert client.get('/api/overview').json()['totals']['runtimes'] == 0
    # snapshots of a deleted runtime go too
    assert client.get('/api/snapshots').json()['snapshots'] == []


def test_delete_unknown_runtime_404(client):
    assert client.delete('/api/runtimes/nope').status_code == 404


def test_unknown_installation_404(client):
    assert client.get("/api/installations/does-not-exist").status_code == 404


def test_snapshots_endpoint(client, fleet_db, monkeypatch):
    rid = _seed(fleet_db, monkeypatch)[0]
    _scan_one(rid, fleet_db)
    snaps = client.get("/api/snapshots").json()["snapshots"]
    assert len(snaps) == 1
    assert snaps[0]["agents_found"] == 1
    assert snaps[0]["payload"]["agents"][0]["agent"] == "codex"


def _all_runtimes(fleet_db):
    with session_scope() as s:
        return [r.id for r in s.execute(select(models.Runtime)).scalars().all()]


async def _scan(rid, settings):
    from agent_fleet.services import persist
    from agent_fleet.services.scanner import scan_runtime, to_ref

    with session_scope() as s:
        ref = to_ref(s.get(models.Runtime, rid))
    result = await scan_runtime(ref, settings)
    persist.persist_result(rid, result, None, settings)


def _scan_one(rid, settings):
    return asyncio.run(_scan(rid, settings))
