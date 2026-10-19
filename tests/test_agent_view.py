"""Agent-first view: version currency and config consistency."""

from __future__ import annotations

import asyncio
import json

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services.updates import compare_versions, version_state


def test_compare_versions():
    assert compare_versions("1.2.3", "1.2.3") == 0
    assert compare_versions("1.2.3", "1.2.4") == -1
    assert compare_versions("1.2.4", "1.2.3") == 1
    assert compare_versions("1.2", "1.2.0") == 0
    assert compare_versions("0.9.9", "1.0.0") == -1
    assert compare_versions("2.1.281", "2.1.302") == -1


def test_version_states():
    assert version_state("1.0.0", "1.0.0") == "latest"
    assert version_state("1.0.0", "2.0.0") == "behind"
    assert version_state("2.0.0", "1.0.0") == "ahead"
    assert version_state("", "1.0.0") == "unknown"
    assert version_state("1.0.0", "") == "unknown"


def test_npm_packages_are_verified_names():
    """These mappings were read from real package.json files, not guessed."""
    from agent_fleet.services.updates import NPM_PACKAGES

    assert NPM_PACKAGES["codex"] == "@openai/codex"
    assert NPM_PACKAGES["claude"] == "@anthropic-ai/claude-code"
    assert NPM_PACKAGES["opencode"] == "opencode-ai"
    assert NPM_PACKAGES["pi"] == "@earendil-works/pi-coding-agent"
    assert NPM_PACKAGES["codebuddy"] == "@tencent-ai/codebuddy-code"
    assert NPM_PACKAGES["qoder"] == "@qoder-ai/qodercli"
    # agy and hermes are not npm-distributed; their feed is GitHub releases
    assert "agy" not in NPM_PACKAGES
    assert "hermes" not in NPM_PACKAGES


def test_github_repos_are_verified_projects():
    """Each repo was checked against a local install's --version output."""
    from agent_fleet.services.updates import GITHUB_REPOS

    assert GITHUB_REPOS["agy"] == "google-antigravity/antigravity-cli"
    assert GITHUB_REPOS["hermes"] == "NousResearch/hermes-agent"
    # nothing is listed in both feeds
    from agent_fleet.services.updates import NPM_PACKAGES

    assert not set(NPM_PACKAGES) & set(GITHUB_REPOS)


def test_github_latest_strips_v_prefix(monkeypatch):
    from agent_fleet.services import updates

    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return {"tag_name": "v0.22.2"}

    monkeypatch.setattr(updates, "_fetch_json", fake_fetch)
    updates.invalidate()
    result = updates.latest_available("hermes")
    assert seen["url"] == (
        "https://api.github.com/repos/NousResearch/hermes-agent/releases/latest"
    )
    assert result["source"] == "github"
    assert result["package"] == "NousResearch/hermes-agent"
    assert result["latest_available"] == "0.22.2"
    assert result["release_url"] == (
        "https://github.com/NousResearch/hermes-agent/releases"
    )
    assert updates.version_state("0.22.2", result["latest_available"]) == "latest"
    updates.invalidate()


def test_github_failure_keeps_intended_source(monkeypatch):
    """A network error must not turn a github agent into 'no feed exists'."""
    from agent_fleet.services import updates

    def boom(url):
        raise TimeoutError("timed out")

    monkeypatch.setattr(updates, "_fetch_json", boom)
    updates.invalidate()
    result = updates.latest_available("agy")
    assert result["source"] == "github"
    assert result["package"] == "google-antigravity/antigravity-cli"
    assert result["latest_available"] is None
    assert "timed out" in result["error"]
    updates.invalidate()


def test_unknown_agent_has_no_feed(monkeypatch):
    from agent_fleet.services import updates

    updates.invalidate()
    result = updates.latest_available("not-an-agent")
    assert result == {
        "source": "unknown",
        "package": None,
        "latest_available": None,
        "release_url": None,
    }


def test_agent_view_consistency_and_currency(client, fleet_db, monkeypatch):
    """Two machines, two different codex versions and models -> flagged."""
    from test_scan import FakeRunner, _codex_stream, _make_runtime
    from test_scan import FakeRunner, _make_runtime

    import asyncio

    from agent_fleet.db import session_scope
    from agent_fleet.services import persist
    from agent_fleet.services.scanner import scan_runtime, to_ref

    # machine A: latest codex with model X
    # machine B: older codex with model Y
    a = _make_runtime(fleet_db, name="av-a")
    b = _make_runtime(fleet_db, name="av-b")

    def runner_for(rt, s):
        if rt.ssh_alias == "av-b":
            stream = _codex_stream().replace("1.2.3", "0.9.0").replace(
                "gpt-5-fixture", "gpt-old"
            )
            return FakeRunner(stream)
        return FakeRunner(_codex_stream())

    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner", runner_for
    )
    # pin the registry answer so the test does not hit the network
    monkeypatch.setattr(
        "agent_fleet.services.updates.latest_available",
        lambda agent_id: {
            "source": "npm",
            "package": "@openai/codex",
            "latest_available": "1.2.3",
        },
    )
    for rid in (a, b):
        with session_scope() as s:
            ref = __import__(
                "agent_fleet.services.scanner", fromlist=["to_ref"]
            ).to_ref(s.get(models.Runtime, rid))
        result = asyncio.run(scan_runtime(ref, fleet_db))
        persist.persist_result(rid, result, None, fleet_db)

    view = client.get("/api/agent-view/codex").json()
    assert view["agent"] == "codex"
    assert view["detected_count"] == 2
    assert view["consistent_configs"] is False
    assert len(view["config_groups"]) == 2

    versions = {i["host"]: i for i in view["installations"]}
    assert versions["av-a"]["version_state"] == "latest"
    assert versions["av-b"]["version_state"] == "behind"
    assert view["version"]["up_to_date"] == 1
    assert view["version"]["behind"] == 1
