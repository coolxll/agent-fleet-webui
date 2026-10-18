"""Diff engine: change records for installations and snapshots."""

from __future__ import annotations

from agent_fleet.services.diff import (
    compare_installations,
    compare_snapshots,
    diff_dicts,
    diff_lists,
)


class _Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Inst:
    def __init__(self, agent="codex", version="1.0.0", exec_path="/bin/codex", **kw):
        self.id = "inst-" + str(id(self))[:6]
        self.agent_id = agent
        self.runtime_id = kw.pop("runtime_id", "rt")
        self.status = kw.pop("status", "installed")
        self.version = version
        self.exec_path = exec_path
        self.install_method = kw.pop("install_method", "npm (nvm)")
        self.config_id = kw.pop("config_id", "cfg-1")
        self.config = kw.pop("config", None)
        self.sources = kw.pop("sources", [])
        for k, v in kw.items():
            setattr(self, k, v)


class _Src:
    def __init__(self, path, status):
        self.path = path
        self.status = status


def _cfg(**kw):
    base = dict(
        model="gpt-5",
        model_provider="custom",
        base_url="https://a.example.com/v1",
        parse_status="parsed",
        mcp_servers=[{"name": "x", "transport": "stdio", "command": "x"}],
        skills=[{"name": "s1"}],
        settings={"approval_policy": "never"},
    )
    base.update(kw)
    return _Cfg(**base)


def test_identical_configs_report_no_changes():
    a = _Inst(config=_cfg())
    b = _Inst(config=_cfg(), exec_path="/bin/codex")
    result = compare_installations(a, b)
    assert result["identical"] is True
    assert result["changes"] == []


def test_two_machine_diff_reports_field_level_changes():
    a = _Inst(config=_cfg(model="gpt-5", model_provider="custom"))
    b = _Inst(
        config=_cfg(
            model="qmodel",
            model_provider="cliproxyapi",
            base_url="https://cpa.example.com/v1",
            mcp_servers=[{"name": "y", "transport": "http", "url": "https://mcp.example.com"}],
            skills=[{"name": "s1"}, {"name": "s2"}],
        ),
        version="0.153.4",
        install_method="system path install",
        exec_path="/usr/local/bin/codex",
        sources=[_Src("~/.codex/config.toml", "ok")],
    )
    result = compare_installations(a, b)
    assert result["identical"] is False
    by_path = {c["path"]: c for c in result["changes"]}
    assert by_path["version"]["kind"] == "changed"
    assert by_path["version"]["a"] == "1.0.0"
    assert by_path["version"]["b"] == "0.153.4"
    assert by_path["model"]["b"] == "qmodel"
    assert by_path["provider"]["b"] == "cliproxyapi"
    assert by_path["exec_path"]["b"] == "/usr/local/bin/codex"
    assert by_path["mcp_servers[x]"]["kind"] == "removed"
    assert by_path["mcp_servers[y]"]["kind"] == "added"
    # skills are flattened to names, so the extra entry appears positionally
    assert any(c["path"].startswith("skills[") and c["b"] == "s2" for c in result["changes"])
    assert result["same_agent"] is True


def test_diff_of_different_agents_is_flagged():
    a = _Inst(agent="codex", config=_cfg())
    b = _Inst(agent="claude", config=_cfg())
    result = compare_installations(a, b)
    assert result["same_agent"] is False


def test_installation_without_config_compares_as_empty():
    a = _Inst(config=None)
    b = _Inst(config=_cfg())
    result = compare_installations(a, b)
    paths = [c["path"] for c in result["changes"]]
    assert "model" in paths
    assert by_kind(result["changes"], "added")


def by_kind(changes, kind):
    return [c for c in changes if c["kind"] == kind]


class _Snap:
    def __init__(self, id, runtime_id, agents):
        self.id = id
        self.runtime_id = runtime_id
        self.created_at = "2026-10-18T00:00:00Z"
        self.agents_found = sum(1 for a in agents if a.get("detected"))
        self.payload = {"agents": agents}


def _agent(agent, **kw):
    entry = {
        "agent": agent,
        "status": "installed",
        "detected": True,
        "version": "1.0.0",
        "exec_path": "/bin/" + agent,
        "install_method": "npm",
        "config_status": "parsed",
        "config": {
            "model": "gpt-5",
            "provider": "custom",
            "network": {"base_url": "https://a.example.com/v1"},
            "mcp_servers": [{"name": "m1"}],
            "skills": [{"name": "s1"}],
            "settings": {"x": 1},
        },
    }
    entry.update(kw)
    return entry


def test_snapshot_diff_no_changes():
    a = _Snap("s1", "rt1", [_agent("codex")])
    b = _Snap("s2", "rt1", [_agent("codex")])
    result = compare_snapshots(a, b)
    assert result["identical"] is True
    assert result["per_agent"] == {}


def test_snapshot_diff_detects_upgrade():
    before = _Snap("s1", "rt1", [_agent("codex", version="1.0.0")])
    after = _Snap("s2", "rt1", [_agent("codex", version="1.2.0")])
    result = compare_snapshots(before, after)
    assert "codex" in result["per_agent"]
    change = next(c for c in result["per_agent"]["codex"] if c["path"] == "version")
    assert change["kind"] == "changed"
    assert change["b"] == "1.2.0"


def test_snapshot_diff_detects_install_and_removal():
    before = _Snap("s1", "rt1", [_agent("codex")])
    after = _Snap("s2", "rt1", [_agent("codex"), _agent("hermes")])
    result = compare_snapshots(before, after)
    assert "hermes" in result["per_agent"]
    assert any(c["kind"] == "added" for c in result["per_agent"]["hermes"])


def test_dict_and_list_diff_basics():
    assert diff_dicts({"a": 1}, {"a": 2}) == [{"path": "a", "kind": "changed", "a": 1, "b": 2}]
    assert diff_dicts({}, {"a": 1}) == [{"path": "a", "kind": "added", "a": None, "b": 1}]
    assert diff_dicts({"a": 1}, {}) == [{"path": "a", "kind": "removed", "a": 1, "b": None}]
    assert diff_dicts({"a": {"b": 1}}, {"a": {"b": 2}}) == [
        {"path": "a.b", "kind": "changed", "a": 1, "b": 2}
    ]
    assert diff_lists([{"name": "x", "v": 1}], [{"name": "x", "v": 2}], "list") == [
        {"path": "list[x].v", "kind": "changed", "a": 1, "b": 2}
    ]
    # lists without a key fall back to positional comparison
    assert diff_lists([1, 2], [1, 3], "n") == [{"path": "n[__1]", "kind": "changed", "a": 2, "b": 3}]


def test_duplicate_named_entries_are_matched_by_value_not_position():
    a = [{"name": "x", "scope": "user", "v": 1}, {"name": "x", "scope": "project", "v": 2}]
    b = [{"name": "x", "scope": "project", "v": 2}, {"name": "x", "scope": "user", "v": 1}]
    # Same entries, different order: no changes.
    assert diff_lists(a, b, "mcp_servers") == []


def test_removing_one_of_two_duplicates_reports_only_that_entry():
    a = [{"name": "x", "v": 1}, {"name": "x", "v": 2}]
    b = [{"name": "x", "v": 2}]
    changes = diff_lists(a, b, "mcp_servers")
    assert len(changes) == 1
    assert changes[0]["kind"] == "removed"
    assert changes[0]["a"] == '{"name": "x", "v": 1}'


def test_changed_duplicate_still_diffs_field_by_field():
    a = [{"name": "x", "v": 1}]
    b = [{"name": "x", "v": 9}]
    changes = diff_lists(a, b, "mcp_servers")
    assert changes == [
        {"path": "mcp_servers[x].v", "kind": "changed", "a": 1, "b": 9}
    ]
