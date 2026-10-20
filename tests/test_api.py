"""API surface tests with an in-memory-ish SQLite file and fake runners."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope

from test_scan import FakeRunner, _codex_stream, _make_runtime
from helpers import fixture_text


def agent_ids():
    from agent_fleet.adapters.registry import agent_ids as _ids

    return _ids()


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


def test_scan_all_skips_excluded_hosts(client, fleet_db, monkeypatch):
    """Hosts excluded from the fleet are not scanned, even with all=True."""
    import agent_fleet.services.scan_service as svc

    from test_scan import FakeRunner, _codex_stream

    ids = []
    for i in range(2):
        rid = _make_runtime(fleet_db, name="scope-" + str(i))
        ids.append(rid)
    with session_scope() as s:
        rt = s.get(models.Runtime, ids[0])
        host = s.get(models.Host, rt.host_id)
        host.enabled = False
        s.commit()
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner", lambda rt, s: FakeRunner(_codex_stream())
    )
    monkeypatch.setattr(
        "agent_fleet.services.scanner.scan_runtime", fake_scan_runtime
    )
    captured = []

    async def fake_start_scan(runtime_ids, triggered_by, settings):
        captured.append(list(runtime_ids))
        return "fake-run"

    monkeypatch.setattr(svc, "start_scan", fake_start_scan)

    res = client.post("/api/scan", json={"all": True})
    assert res.status_code == 200
    assert res.json()["total"] == 1  # the excluded host's runtime is not scanned


def fake_scan_runtime(ref, settings):
    raise AssertionError("scanner must not be reached for excluded hosts")


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
    assert len(inst) == len(agent_ids())

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


TAB = chr(9)
NL = chr(10)


def _agy_swap_stream():
    accounts = fixture_text("agy", "agy_swap_accounts.json")
    config = fixture_text("agy", "agy_swap_config.json")
    body = [
        "AF" + TAB + "os" + TAB + "Linux",
        "AF" + TAB + "arch" + TAB + "x86_64",
        "AF" + TAB + "home" + TAB + "/home/fixture",
        "AF" + TAB + "user" + TAB + "fixture",
        "AF" + TAB + "probed" + TAB + "agy:agy",
        "AF" + TAB + "which" + TAB + "agy:agy" + TAB + "/home/fixture/.local/bin/agy",
        "AF" + TAB + "version" + TAB + "agy:agy" + TAB + "0" + TAB + "1.4.2",
        "AF" + TAB + "file" + TAB + "~/.gemini/agy-swap/accounts.json" + TAB
        + str(len(accounts)) + TAB + "present",
    ]
    body += accounts.split(NL)
    body += [
        "AF" + TAB + "endfile" + TAB + "~/.gemini/agy-swap/accounts.json",
        "AF" + TAB + "file" + TAB + "~/.gemini/agy-swap/config.json" + TAB
        + str(len(config)) + TAB + "present",
    ]
    body += config.split(NL)
    body += [
        "AF" + TAB + "endfile" + TAB + "~/.gemini/agy-swap/config.json",
        "AF" + TAB + "done" + TAB + "1",
    ]
    return NL.join(body)


def test_agy_swap_quota_reaches_api_agent_view_and_export(client, fleet_db, monkeypatch):
    rid = _make_runtime(fleet_db, name="agy-box")
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner",
        lambda rt, s: FakeRunner(_agy_swap_stream()),
    )
    _scan_one(rid, fleet_db)

    insts = client.get("/api/installations?agent_id=agy").json()["installations"]
    assert len(insts) == 1
    detail = client.get("/api/installations/" + insts[0]["id"]).json()
    quota = detail["config"]["quota"]
    assert quota["source"] == "agy-swap"
    assert [a["email"] for a in quota["accounts"]] == [
        "alice@example.com",
        "bob@example.com",
    ]
    assert detail["config_summary"]["quota"]["account_count"] == 2
    assert detail["config_summary"]["quota"]["min_remaining_fraction"] == 0.01

    blob = str(detail)
    assert "fixture-access-token-must-not-leak" not in blob
    assert "fixture-token-hash-must-not-leak" not in blob
    assert "secret_ref" not in blob

    view = client.get("/api/agent-view/agy").json()
    agy_inst = next(i for i in view["installations"] if i["id"] == insts[0]["id"])
    assert agy_inst["quota"]["accounts"][0]["aliases"] == ["work"]

    exported = client.get("/api/export?agent_id=agy").json()
    agent = exported["hosts"][0]["agents"][0]
    assert agent["config"]["quota"]["accounts"][1]["email"] == "bob@example.com"

    quotas = client.get("/api/quotas").json()
    assert quotas["providers"] == [
        {"agent": "agy", "name": "Antigravity (AGY)", "installations": 1, "accounts": 2}
    ]
    assert len(quotas["quotas"]) == 1
    entry = quotas["quotas"][0]
    assert entry["host"] == "agy-box"
    assert entry["quota"]["accounts"][0]["aliases"] == ["work"]
    assert entry["quota_summary"]["min_remaining_fraction"] == 0.01


def test_quota_account_import_refresh_and_delete(client, fleet_db, monkeypatch):
    from agent_fleet.services import quota_providers

    async def fake_fetch(provider, raw):
        assert provider == "codex"
        assert "fixture-codex-credential" in raw
        return {
            "credential": {"access_token": "fixture-codex-credential"},
            "identity": {"email": "quota@example.com", "name": "Quota User", "plan": "team"},
            "quota": {
                "source": "codex",
                "observed_at": "2026-10-20T12:00:00Z",
                "accounts": [
                    {
                        "email": "quota@example.com",
                        "name": "Quota User",
                        "tier": {"id": "team", "name": "team"},
                        "observed_at": "2026-10-20T12:00:00Z",
                        "groups": [
                            {
                                "id": "primary",
                                "name": "5-Hour limit",
                                "buckets": [
                                    {
                                        "id": "primary",
                                        "name": "5-Hour",
                                        "window": "5h",
                                        "remaining_fraction": 0.75,
                                        "reset_at": "2026-10-20T17:00:00Z",
                                    }
                                ],
                            }
                        ],
                        "min_remaining_fraction": 0.75,
                    }
                ],
            },
        }

    monkeypatch.setattr(quota_providers, "fetch_account", fake_fetch)

    created = client.post(
        "/api/quota-accounts",
        json={
            "provider": "codex",
            "label": "work",
            "credential": "fixture-codex-credential",
        },
    )
    assert created.status_code == 200
    account = created.json()
    assert account["email"] == "quota@example.com"
    assert account["label"] == "work"
    assert account["quota_summary"]["min_remaining_fraction"] == 0.75
    assert "credential" not in account
    assert "fixture-codex-credential" not in str(account)

    store = fleet_db.db_path.parent / "quota_accounts.json"
    assert store.exists()
    assert oct(store.stat().st_mode & 0o777) == "0o600"

    listed = client.get("/api/quota-accounts").json()["accounts"]
    assert [a["id"] for a in listed] == [account["id"]]

    refreshed = client.post(f"/api/quota-accounts/{account['id']}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "ok"

    deleted = client.delete(f"/api/quota-accounts/{account['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/quota-accounts").json()["accounts"] == []
