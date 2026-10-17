"""Export: shape, filters, and the guarantee that nothing raw leaks."""

from __future__ import annotations

import asyncio
import json

from agent_fleet.db import session_scope
from agent_fleet.services.export import build_export
from agent_fleet.services.scanner import scan_runtime
from test_scan import FakeRunner, _codex_stream, _make_runtime, _ref


def _seed(settings, monkeypatch, count=2):
    ids = [_make_runtime(settings, name="exp-" + str(i)) for i in range(count)]
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner", lambda rt, s: FakeRunner(_codex_stream())
    )
    for rid in ids:
        result = asyncio.run(scan_runtime(_ref(rid), settings))
        from agent_fleet.services import persist

        persist.persist_result(rid, result, None, settings)
    return ids


def test_export_shape(fleet_db, monkeypatch):
    _seed(fleet_db, monkeypatch, count=2)
    doc = build_export()
    assert doc["schema"] == "agent-fleet-export/1"
    assert doc["generated_at"]
    assert doc["counts"]["runtimes"] == 2
    assert doc["counts"]["installations"] == 10
    host = doc["hosts"][0]
    assert host["host"].startswith("exp-")
    assert host["runtime"]["kind"] == "linux"
    agents = {a["agent"]: a for a in host["agents"]}
    assert set(agents) == {"codex", "claude", "opencode", "pi", "hermes"}
    codex = agents["codex"]
    assert codex["version"] == "1.2.3"
    assert codex["config"]["model"] == "gpt-5-fixture"
    assert codex["config"]["resolution"] in ("declared", "effective")
    assert codex["sources"][0]["path"]


def test_export_filters(fleet_db, monkeypatch):
    _seed(fleet_db, monkeypatch, count=2)
    by_agent = build_export(agent_id="codex")
    assert by_agent["counts"]["installations"] == 2
    assert all(set(a["agent"] for a in h["agents"]) == {"codex"} for h in by_agent["hosts"])
    detected = build_export(detected_only=True)
    assert detected["counts"]["installations"] == 2
    assert all(a["detected"] for h in detected["hosts"] for a in h["agents"])
    one = build_export(runtime_id=by_agent["hosts"][0]["runtime"]["id"])
    assert one["counts"]["runtimes"] == 1


def test_export_has_no_credentials(fleet_db, monkeypatch):
    _seed(fleet_db, monkeypatch, count=1)
    doc = build_export()
    blob = json.dumps(doc)
    assert "sk-" not in blob


def test_export_is_json_serializable(fleet_db, monkeypatch):
    _seed(fleet_db, monkeypatch, count=1)
    doc = build_export()
    # the API returns this dict directly, so it must survive json.dumps
    json.dumps(doc)
    assert isinstance(doc["hosts"][0]["runtime"]["last_success_at"], str)
