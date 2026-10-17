"""Scope discovery and precedence: system < user < project."""

from __future__ import annotations

import json

from agent_fleet.adapters.registry import get_adapter
from helpers import fixture_text, make_facts

CLAUDE_SETTINGS = fixture_text("claude", "settings.json")
CLAUDE_GLOBAL = fixture_text("claude", "global.json")


def _claude(extra_files=None, settings=None, global_cfg=None):
    files = {
        "/etc/claude-code/managed-settings.json": None,
        "~/.claude/settings.json": ("ok", settings if settings is not None else CLAUDE_SETTINGS),
        "~/.claude.json": ("ok", global_cfg if global_cfg is not None else CLAUDE_GLOBAL),
        "~/.claude/settings.local.json": None,
    }
    files.update(extra_files or {})
    return make_facts(files=files, probed={"claude:claude"})


def test_project_roots_come_from_the_user_state_file():
    facts = _claude()
    adapter = get_adapter("claude")
    roots = adapter.project_roots(facts)
    assert roots == ["/work/a", "/work/b"]


def test_project_scope_files_are_probed_and_reported():
    facts = _claude(extra_files={"/work/a/.claude/settings.json": None})
    res = get_adapter("claude").run(facts)
    scopes = res.config.sanitized["scopes"]
    project = [s for s in scopes if s["scope"] == "project"]
    assert any(s["path"] == "/work/a/.claude/settings.json" and s["status"] == "missing" for s in project)
    assert any(s["path"] == "/etc/claude-code/managed-settings.json" for s in scopes if s["scope"] == "system")


def test_project_settings_override_user_settings():
    project_settings = json.dumps(
        {
            "env": {"ANTHROPIC_MODEL": "project-model", "ANTHROPIC_BASE_URL": "https://p.example.com/v1"},
            "effortLevel": "low",
        }
    )
    facts = _claude(extra_files={"/work/a/.claude/settings.json": ("ok", project_settings)})
    cfg = get_adapter("claude").run(facts).config
    # project scope wins over user scope
    assert cfg.model == "project-model"
    assert cfg.base_url == "https://p.example.com/v1"
    assert cfg.settings["effortLevel"] == "low"
    assert cfg.resolution == "effective"


def test_project_mcp_json_servers_are_marked_as_project_scope():
    project_mcp = json.dumps(
        {"mcpServers": {"repo-only": {"command": "repo-mcp", "args": ["serve"]}}}
    )
    facts = _claude(extra_files={"/work/a/.mcp.json": ("ok", project_mcp)})
    cfg = get_adapter("claude").run(facts).config
    server = next(m for m in cfg.mcp_servers if m["name"] == "repo-only")
    assert server["scope"] == "project"
    assert server["source"] == "/work/a/.mcp.json"
    assert server["command"] == "repo-mcp"


def test_unreadable_scope_blocks_the_effective_claim():
    facts = _claude(extra_files={"/work/a/.claude/settings.json": ("unreadable", None)})
    cfg = get_adapter("claude").run(facts).config
    assert cfg.resolution == "declared"
    assert cfg.settings["scope_resolution"] == "declared"
    assert any("could not be resolved" in w for w in cfg.warnings)


def test_no_projects_is_reported_honestly():
    facts = _claude(global_cfg=json.dumps({"mcpServers": {}}))
    cfg = get_adapter("claude").run(facts).config
    project = [s for s in cfg.sanitized["scopes"] if s["scope"] == "project"]
    assert project and all(s["status"] == "no_projects" for s in project)


def test_codex_project_trust_is_reported_without_secrets():
    toml = (
        'model = "m"' + chr(10)
        + '[projects."/Users/x/repo"]' + chr(10)
        + 'trust_level = "trusted"' + chr(10)
        + '[projects."/Users/x/other"]' + chr(10)
        + 'trust_level = "untrusted"' + chr(10)
    )
    facts = make_facts(
        files={
            "/etc/codex/config.toml": None,
            "~/.codex/config.toml": ("ok", toml),
            "~/.codex/AGENTS.md": None,
        },
        probed={"codex:codex"},
    )
    cfg = get_adapter("codex").run(facts).config
    assert cfg.settings["projects"] == ["/Users/x/other", "/Users/x/repo"]
    assert cfg.settings["trusted_projects"] == 1
    assert cfg.resolution == "effective"


def test_opencode_project_files_are_discovered():
    facts = make_facts(
        files={
            "/etc/opencode/opencode.json": None,
            "~/.config/opencode/opencode.json": ("ok", json.dumps({"model": "p/m"})),
            "~/.config/opencode/opencode.jsonc": None,
        },
        probed={"opencode:opencode"},
    )
    adapter = get_adapter("opencode")
    # no project registry in the user config -> no project roots
    assert adapter.project_roots(facts) == []
    cfg = adapter.run(facts).config
    assert cfg.resolution == "effective"


def test_pi_and_hermes_report_system_scope():
    for agent, path, content in (
        ("pi", "~/.pi/agent/settings.json", fixture_text("pi", "settings.json")),
        ("hermes", "~/.hermes/config.yaml", fixture_text("hermes", "config.yaml")),
    ):
        system_path = "/etc/pi/agent/settings.json" if agent == "pi" else "/etc/hermes/config.yaml"
        facts = make_facts(
            files={system_path: None, path: ("ok", content)},
            probed={agent + ":" + agent},
        )
        cfg = get_adapter(agent).run(facts).config
        scopes = cfg.sanitized["scopes"]
        assert [s["scope"] for s in scopes] == ["system", "user"]
        assert scopes[0]["status"] == "missing"
        assert scopes[1]["status"] == "ok"
        assert cfg.resolution == "effective"
