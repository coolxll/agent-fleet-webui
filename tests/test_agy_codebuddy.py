"""Antigravity (AGY) and CodeBuddy adapters, fixture-verified."""

from __future__ import annotations

import json

from agent_fleet.adapters.registry import get_adapter
from helpers import fixture_text, make_facts


CLI_SETTINGS = "~/.gemini/antigravity-cli/settings.json"
CLI_MCP = "~/.gemini/antigravity-cli/mcp_config.json"
OLD_MCP = "~/.gemini/antigravity/mcp_config.json"
SHARED_MCP = "~/.gemini/config/mcp_config.json"
STATE = "~/.gemini/antigravity/antigravity_state.pbtxt"


def _agy_facts(
    mcp=None,
    shared=None,
    state=None,
    cli_settings=None,
    cli_mcp=None,
    with_binary=True,
    version="1.4.2",
    cli_dir=True,
):
    """Facts for the current (1.4.2+) layout by default.

    ``cli_settings``/``cli_mcp`` may be set to None to model a partially
    present CLI directory; ``cli_dir=False`` omits the whole directory tree,
    which is what selects the pre-1.4.2 layout.
    """
    files = {
        SHARED_MCP: ("ok", shared if shared is not None else fixture_text("agy", "shared_mcp_config.json")),
        STATE: ("ok", state if state is not None else fixture_text("agy", "state.pbtxt")),
        OLD_MCP: ("ok", mcp if mcp is not None else fixture_text("agy", "mcp_config.json")),
    }
    if cli_dir:
        if cli_settings is not False:
            files[CLI_SETTINGS] = (
                "ok",
                cli_settings
                if cli_settings is not None
                else fixture_text("agy", "cli_settings.json"),
            )
        if cli_mcp is not False:
            files[CLI_MCP] = (
                "ok",
                cli_mcp if cli_mcp is not None else fixture_text("agy", "cli_mcp_config.json"),
            )
    skills = {
        "~/.gemini/config/skills": [
            {"name": "config-skill", "path": "config-skill", "skill_file": "x"}
        ],
        "~/.gemini/antigravity/skills": [
            {"name": "agy-skill", "path": "agy-skill", "skill_file": "x"}
        ],
        "~/.agents/skills": [
            {"name": "shared-skill", "path": "shared-skill", "skill_file": "y"}
        ],
    }
    if cli_dir:
        skills["~/.gemini/antigravity-cli/skills"] = [
            {"name": "cli-skill", "path": "cli-skill", "skill_file": "x"}
        ]
    return make_facts(
        files=files,
        which={"agy:agy": "/Users/fixture/.local/bin/agy"} if with_binary else {},
        versions={"agy:agy": (0, version)},
        probed={"agy:agy"},
        skills=skills,
    )


def test_agy_installed_and_parsed():
    res = get_adapter("agy").run(_agy_facts())

    assert res.detection.status == "installed"
    assert res.detection.detected is True
    assert res.detection.version == "1.4.2"
    assert res.detection.exec_path == "/Users/fixture/.local/bin/agy"
    assert res.detection.install_method == "user-local install"
    assert res.detection.config_dir == "~/.gemini/antigravity-cli"

    cfg = res.config
    assert cfg.parse_status == "parsed"
    # 1.4.2+ model comes from the CLI settings, not the internal state id
    assert cfg.model == "Gemini 3.8 Flash (High)"
    assert cfg.settings["model_source"] == "antigravity-cli/settings.json"
    assert not any("internal agent-model id" in w for w in cfg.warnings)

    by_name = {m["name"]: m for m in cfg.mcp_servers}
    # the 1.4.2 layout is exclusive: the stale pre-1.4.2 file is not merged in
    assert set(by_name) == {
        "fixture-cli",
        "fixture-stdio",
        "fixture-search",
        "fixture-search-disabled",
    }
    stdio = by_name["fixture-stdio"]
    assert stdio["scope"] == "user"
    assert stdio["source"] == CLI_MCP
    assert stdio["command"] == "/usr/local/bin/fixture-cli-mcp"
    cli_http = by_name["fixture-cli"]
    assert cli_http["scope"] == "user"
    assert cli_http["source"] == CLI_MCP
    assert cli_http["env_keys"] == ["Authorization"]
    http = by_name["fixture-search"]
    assert http["scope"] == "shared"
    # serverUrl with a token in the query string must be masked
    assert http["url"] == "http://fixture-gateway.internal:8790/mcp"
    assert http["url_masked"] is True
    assert http["env_keys"] == ["Authorization"]
    # the disabled flag still survives from the shared file
    assert by_name["fixture-search-disabled"]["enabled"] is False
    # ...but the stale IDE file's chrome-devtools is not reported at all
    assert "chrome-devtools" not in by_name

    skill_names = [s["name"] for s in cfg.skills]
    # legacy skills dir is not merged into the new layout
    assert skill_names == ["cli-skill", "config-skill", "shared-skill"]
    assert cfg.settings["skills_dirs"] == [
        "~/.gemini/antigravity-cli/skills",
        "~/.gemini/config/skills",
        "~/.agents/skills",
    ]
    assert cfg.settings["layout"] == "cli"
    assert "conversations" in cfg.unsupported_fields
    # a disabled flag survives; the shared-file copy is still reported

    # scope report must not advertise the inactive legacy user file
    scopes = {s["path"]: s for s in cfg.settings["scopes"]}
    assert set(scopes) == {CLI_MCP, SHARED_MCP}
    assert scopes[SHARED_MCP]["scope"] == "shared"


def test_agy_pre_142_layout_falls_back():
    """Older installs (no antigravity-cli dir) still parse the old paths."""
    facts = _agy_facts(cli_dir=False, version="1.4.1")
    res = get_adapter("agy").run(facts)
    cfg = res.config

    assert res.detection.config_dir == "~/.gemini/antigravity"
    # falls back to the protobuf state, with the warning restored
    assert cfg.model == "MODEL_PLACEHOLDER_M319"
    assert cfg.settings["model_source"] == "antigravity_state.pbtxt"
    assert any("internal agent-model id" in w for w in cfg.warnings)

    by_name = {m["name"]: m for m in cfg.mcp_servers}
    # old user file is used because the CLI copy is absent
    assert by_name["fixture-stdio"]["source"] == OLD_MCP
    assert by_name["fixture-stdio"]["env_keys"] == ["FIXTURE_API_KEY"]
    assert by_name["chrome-devtools"]["enabled"] is False
    # the shared file is always read
    assert by_name["fixture-search"]["scope"] == "shared"
    # legacy skills dir is the one reported in this layout
    assert [s["name"] for s in cfg.skills] == ["agy-skill", "shared-skill"]
    assert cfg.settings["layout"] == "legacy"


def test_agy_cli_mcp_without_settings():
    """A missing settings.json must not hide the MCP files."""
    facts = _agy_facts(cli_settings=False)
    cfg = get_adapter("agy").run(facts).config
    assert cfg.model == "MODEL_PLACEHOLDER_M319"
    names = {m["name"] for m in cfg.mcp_servers}
    assert "fixture-cli" in names
    assert "fixture-search" in names


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
    facts = _agy_facts(
        mcp="{ }",
        shared='{"mcpServers": {}}',
        state="nothing relevant\n",
        cli_settings="{ }",
        cli_mcp='{"mcpServers": {}}',
    )
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
