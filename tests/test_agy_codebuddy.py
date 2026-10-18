"""Antigravity (AGY) and CodeBuddy adapters, fixture-verified."""

from __future__ import annotations

import json

from agent_fleet.adapters.registry import get_adapter
from helpers import fixture_text, make_facts


def _agy_facts(mcp=None, shared=None, state=None, with_binary=True):
    files = {
        "~/.gemini/antigravity/mcp_config.json": ("ok", mcp if mcp is not None else fixture_text("agy", "mcp_config.json")),
        "~/.gemini/config/mcp_config.json": ("ok", shared if shared is not None else fixture_text("agy", "shared_mcp_config.json")),
        "~/.gemini/antigravity/antigravity_state.pbtxt": ("ok", state if state is not None else fixture_text("agy", "state.pbtxt")),
    }
    return make_facts(
        files=files,
        which={"agy:agy": "/Users/fixture/.local/bin/agy"} if with_binary else {},
        versions={"agy:agy": (0, "1.4.1")},
        probed={"agy:agy"},
        skills={
            "~/.gemini/antigravity/skills": [
                {"name": "agy-skill", "path": "agy-skill", "skill_file": "x"}
            ],
            "~/.agents/skills": [
                {"name": "shared-skill", "path": "shared-skill", "skill_file": "y"}
            ],
        },
    )


def test_agy_installed_and_parsed():
    res = get_adapter("agy").run(_agy_facts())

    assert res.detection.status == "installed"
    assert res.detection.detected is True
    assert res.detection.version == "1.4.1"
    assert res.detection.exec_path == "/Users/fixture/.local/bin/agy"
    assert res.detection.install_method == "user-local install"
    assert res.detection.config_dir == "~/.gemini/antigravity"

    cfg = res.config
    assert cfg.parse_status == "parsed"
    # model comes from the protobuf-text state file
    assert cfg.model == "MODEL_PLACEHOLDER_M319"
    assert cfg.settings["model_source"] == "antigravity_state.pbtxt"
    assert any("internal agent-model id" in w for w in cfg.warnings)

    by_name = {m["name"]: m for m in cfg.mcp_servers}
    assert set(by_name) == {"chrome-devtools", "fixture-stdio", "fixture-search"}
    stdio = by_name["fixture-stdio"]
    assert stdio["scope"] == "user"
    assert stdio["command"] == "/usr/local/bin/fixture-mcp"
    assert stdio["env_keys"] == ["FIXTURE_API_KEY"]
    http = by_name["fixture-search"]
    assert http["scope"] == "shared"
    # serverUrl with a token in the query string must be masked
    assert http["url"] == "http://fixture-gateway.internal:8790/mcp"
    assert http["url_masked"] is True
    assert http["env_keys"] == ["Authorization"]
    disabled = by_name["chrome-devtools"]
    assert disabled["enabled"] is False

    skill_names = [s["name"] for s in cfg.skills]
    assert skill_names == ["agy-skill", "shared-skill"]
    assert cfg.settings["skills_dirs"] == [
        "~/.gemini/antigravity/skills",
        "~/.agents/skills",
    ]
    assert "conversations" in cfg.unsupported_fields


def test_agy_never_leaks_credentials():
    res = get_adapter("agy").run(_agy_facts())
    blob = json.dumps(res.config.sanitized)
    assert "sk-fixture" not in blob
    assert "b442bcbd" not in blob
    assert "Bearer" not in blob
    # header names are reported, values are not
    assert "Authorization" in blob
    # the masked URL must not carry the query string
    assert "token=" not in blob


def test_agy_empty_config():
    facts = _agy_facts(mcp="{ }", shared='{"mcpServers": {}}', state="nothing relevant\n")
    res = get_adapter("agy").run(facts)
    assert res.config.parse_status == "empty"
    assert res.config.model is None
    assert res.config.mcp_servers == []


def test_agy_absent_binary():
    facts = _agy_facts(with_binary=False)
    res = get_adapter("agy").run(facts)
    assert res.detection.status == "not_installed"


def test_agy_never_probed_is_unknown():
    facts = make_facts(files={"~/.gemini/antigravity/mcp_config.json": ("ok", "{}")})
    res = get_adapter("agy").run(facts)
    assert res.detection.status == "unknown"


# ---------------------------------------------------------------------------
# CodeBuddy
# ---------------------------------------------------------------------------


def _cb_facts(settings=None, marketplaces=None, with_binary=True, skills=None):
    files = {
        "~/.codebuddy/settings.json": ("ok", settings if settings is not None else fixture_text("codebuddy", "settings.json")),
        "~/.codebuddy/plugins/known_marketplaces.json": ("ok", marketplaces if marketplaces is not None else fixture_text("codebuddy", "known_marketplaces.json")),
    }
    return make_facts(
        files=files,
        which={"codebuddy:codebuddy": "/Users/fixture/.nvm/versions/node/v24.14.1/bin/codebuddy"} if with_binary else {},
        versions={"codebuddy:codebuddy": (0, "2.168.2")},
        probed={"codebuddy:codebuddy"},
        skills=skills or {},
    )


def test_codebuddy_installed_and_parsed():
    res = get_adapter("codebuddy").run(_cb_facts())

    assert res.detection.status == "installed"
    assert res.detection.version == "2.168.2"
    assert res.detection.install_method == "npm (nvm)"
    assert res.detection.config_dir == "~/.codebuddy"

    cfg = res.config
    assert cfg.parse_status == "parsed"
    assert cfg.model == "fixture-model-v2"
    assert cfg.settings["hooks"] == ["SessionStart", "Stop"]
    assert cfg.settings["trustedDirectories"] == 2  # lists are reported as counts
    assert cfg.settings["marketplaces"] == ["fixture-marketplace", "fixture-official"]
    assert "apiKey" in cfg.unsupported_fields
    assert "somethingUnknown" in cfg.unsupported_fields
    # no user-scope MCP on this machine, and we say so instead of inventing one
    assert cfg.mcp_servers == []


def test_codebuddy_never_leaks_credentials():
    res = get_adapter("codebuddy").run(_cb_facts())
    blob = json.dumps(res.config.sanitized)
    assert "sk-fixture" not in blob
    assert "apiKey" in res.config.unsupported_fields


def test_codebuddy_project_mcp_servers_are_discovered():
    project_mcp = json.dumps(
        {"mcpServers": {"repo-only": {"command": "repo-mcp", "args": ["serve"]}}}
    )
    facts = make_facts(
        files={
            "~/.codebuddy/settings.json": ("ok", fixture_text("codebuddy", "settings.json")),
            "~/.codebuddy/plugins/known_marketplaces.json": None,
            "/Users/fixture/work/a/.mcp.json": ("ok", project_mcp),
        },
        which={"codebuddy:codebuddy": "/usr/local/bin/codebuddy"},
        versions={"codebuddy:codebuddy": (0, "2.168.2")},
        probed={"codebuddy:codebuddy"},
    )
    cfg = get_adapter("codebuddy").run(facts).config
    server = next(m for m in cfg.mcp_servers if m["name"] == "repo-only")
    assert server["scope"] == "project"
    assert server["source"] == "/Users/fixture/work/a/.mcp.json"
    assert server["command"] == "repo-mcp"


def test_codebuddy_missing_settings():
    facts = make_facts(
        files={
            "~/.codebuddy/settings.json": None,
            "~/.codebuddy/plugins/known_marketplaces.json": None,
        },
        probed={"codebuddy:codebuddy"},
    )
    res = get_adapter("codebuddy").run(facts)
    assert res.detection.status == "not_installed"
    assert res.config.parse_status == "empty"


def test_codebuddy_broken_settings_json():
    facts = _cb_facts(settings="{ this is not json")
    res = get_adapter("codebuddy").run(facts)
    assert res.config.parse_status in ("empty", "partial", "error")
