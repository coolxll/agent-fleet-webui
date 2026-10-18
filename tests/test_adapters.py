"""Adapter tests driven entirely by fixtures: no machine, no network."""

from __future__ import annotations

import json

import pytest

from agent_fleet.adapters.registry import all_adapters, get_adapter
from agent_fleet.remotes.probe import FileFact, RemoteFacts
from helpers import fixture_text, make_facts

SECRET_MARKERS = (
    "sk-fixture",
    "0123456789abcdef",
    "Bearer sk-",
    "FIXTURE_API_KEY_VALUE",
)


def assert_clean(payload) -> None:
    """Nothing that looks like a credential may survive sanitization."""
    blob = json.dumps(payload, default=str)
    for marker in SECRET_MARKERS:
        assert marker not in blob, f"leaked marker {marker!r} in {blob[:400]}"
    assert "sk-" not in blob


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


def _codex_facts(config: str | None = None, status: str = "ok"):
    files = {
        "~/.codex/config.toml": (status, config) if config is not None else None,
        "~/.codex/AGENTS.md": None,
        # system policy path: probed, absent (the normal case)
        "/etc/codex/config.toml": None,
    }
    return make_facts(
        files=files,
        which={"codex:codex": "/usr/local/bin/codex"},
        versions={"codex:codex": (0, "codex-cli 0.99.1")},
        probed={"codex:codex"},
    )


def test_codex_installed_and_parsed():
    facts = _codex_facts(fixture_text("codex", "config.toml"))
    res = get_adapter("codex").run(facts)

    assert res.detection.status == "installed"
    assert res.detection.detected is True
    assert res.detection.version == "0.99.1"
    assert res.detection.exec_path == "/usr/local/bin/codex"
    assert res.detection.install_method == "system path install"

    cfg = res.config
    assert cfg.parse_status == "parsed"
    assert cfg.model == "gpt-5.1-codex"
    assert cfg.model_provider == "fixture-provider"
    # query string carrying a token must be stripped
    assert cfg.base_url == "https://fixture.example.com/v1"
    assert cfg.base_url_masked is True
    assert cfg.settings["approval_policy"] == "on-request"
    assert cfg.settings["sandbox_mode"] == "workspace-write"

    names = sorted(m["name"] for m in cfg.mcp_servers)
    assert names == ["fixture-http", "fixture-stdio"]
    stdio = next(m for m in cfg.mcp_servers if m["name"] == "fixture-stdio")
    assert stdio["transport"] == "stdio"
    assert stdio["command"] == "/usr/local/bin/fixture-mcp"
    assert stdio["env_keys"] == ["FIXTURE_API_KEY", "FIXTURE_REGION"]
    http = next(m for m in cfg.mcp_servers if m["name"] == "fixture-http")
    assert http["url"] == "https://mcp.fixture.example.com/mcp"
    assert http["env_keys"] == ["Authorization"]

    skill_names = [s["name"] for s in cfg.skills]
    assert "fixture-skill" in skill_names
    assert "disabled-skill" in skill_names

    assert_clean(res.config.sanitized)
    # the field is reported by name (so the user knows it exists) but never by value
    assert "experimental_bearer_token" in cfg.unsupported_fields
    assert any("bearer token" in w for w in cfg.warnings)


def test_codex_missing_config_is_not_installed():
    facts = _codex_facts(None)
    res = get_adapter("codex").run(facts)
    # binary present but no config file at all
    assert res.detection.status == "installed"
    assert res.config.parse_status == "empty"
    assert res.sources[0].status == "missing"


def test_codex_absent_binary():
    facts = make_facts(files={"~/.codex/config.toml": None}, probed={"codex:codex"})
    res = get_adapter("codex").run(facts)
    assert res.detection.status == "not_installed"
    assert res.detection.detected is False


def test_codex_never_probed_is_unknown():
    facts = make_facts(files={"~/.codex/config.toml": ("ok", "model = 'x'")})
    res = get_adapter("codex").run(facts)
    assert res.detection.status == "unknown"


def test_codex_broken_toml():
    facts = _codex_facts(fixture_text("codex", "broken.toml"))
    res = get_adapter("codex").run(facts)
    assert res.config.parse_status == "error"
    assert any("parse error" in w for w in res.config.warnings)


def test_codex_unreadable_file():
    facts = _codex_facts("irrelevant", status="unreadable")
    facts.files["~/.codex/config.toml"] = FileFact(
        path="~/.codex/config.toml", status="unreadable", size=0, content=None
    )
    res = get_adapter("codex").run(facts)
    assert res.config.parse_status == "error"
    user_src = next(s for s in res.sources if s.path == "~/.codex/config.toml")
    assert user_src.status in ("unreadable", "error")
    # an unreadable scope must stop us from claiming effective values
    assert res.config.resolution == "declared"
    assert any("could not be resolved" in w for w in res.config.warnings)


def test_codex_system_policy_is_reported_as_absent():
    facts = _codex_facts(fixture_text("codex", "config.toml"))
    res = get_adapter("codex").run(facts)
    system = next(s for s in res.sources if s.scope == "system")
    assert system.path == "/etc/codex/config.toml"
    assert system.status == "missing"
    assert res.config.resolution == "effective"


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


def _claude_facts(settings=None, global_cfg=None):
    files = {
        "~/.claude/settings.json": settings,
        "~/.claude.json": global_cfg,
        "~/.claude/settings.local.json": None,
    }
    return make_facts(
        files=files,
        which={"claude:claude": "/opt/homebrew/bin/claude"},
        versions={"claude:claude": (0, "2.1.100 (Claude Code)")},
        probed={"claude:claude"},
        skills={
            "~/.claude/skills": [
                {"name": "fixture-skill", "path": "fixture-skill", "skill_file": "x"}
            ]
        },
        dirs={"~/.claude/plugins": "dir"},
    )


def test_claude_installed_and_parsed():
    facts = _claude_facts(
        fixture_text("claude", "settings.json"), fixture_text("claude", "global.json")
    )
    res = get_adapter("claude").run(facts)

    assert res.detection.status == "installed"
    assert res.detection.version == "2.1.100"
    assert res.detection.install_method == "homebrew"

    cfg = res.config
    # per-project servers are reported separately, not merged into user scope
    assert cfg.parse_status == "parsed"
    assert any("per-project MCP servers" in w for w in cfg.warnings)
    assert cfg.model == "fixture-sonnet"
    assert cfg.model_provider == "custom"
    assert cfg.base_url == "https://fixture-claude.example.com/v1"
    assert cfg.base_url_masked is False

    names = sorted(m["name"] for m in cfg.mcp_servers)
    assert names == ["fixture-local", "fixture-search"]
    local = next(m for m in cfg.mcp_servers if m["name"] == "fixture-local")
    assert local["command"] == "fixture-mcp"
    assert local["env_keys"] == ["FIXTURE_TOKEN"]
    search = next(m for m in cfg.mcp_servers if m["name"] == "fixture-search")
    assert search["transport"] == "url"
    assert search["env_keys"] == ["Authorization"]

    assert cfg.settings["project_scoped_mcp_servers"] == ["project-only"]
    assert cfg.settings["project_count"] == 2
    assert cfg.settings["effortLevel"] == "high"
    assert [s["name"] for s in cfg.skills] == ["fixture-skill"]
    assert "somethingUnknown" in cfg.unsupported_fields

    # env values never travel, only their names
    assert_clean(res.config.sanitized)
    env_blob = json.dumps(res.config.sanitized)
    assert "ANTHROPIC_AUTH_TOKEN" in env_blob
    assert "sk-fixture-claude-token" not in env_blob


def test_claude_defaults_to_anthropic_without_base_url():
    facts = _claude_facts(json.dumps({"env": {"ANTHROPIC_MODEL": "m"}}), None)
    cfg = get_adapter("claude").run(facts).config
    assert cfg.model_provider == "anthropic"
    assert cfg.base_url is None


def test_claude_missing_settings_file():
    facts = _claude_facts(None, None)
    res = get_adapter("claude").run(facts)
    assert res.config.parse_status in ("empty", "partial")


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------


def _opencode_facts(cfg=None):
    files = {
        "~/.config/opencode/opencode.json": cfg,
        "~/.config/opencode/opencode.jsonc": None,
        "/etc/opencode/opencode.json": None,
    }
    return make_facts(
        files=files,
        which={"opencode:opencode": "/home/fixture/.opencode/bin/opencode"},
        versions={"opencode:opencode": (0, "1.20.0")},
        probed={"opencode:opencode"},
        skills={
            "~/.config/opencode/skills": [
                {"name": "oc-skill", "path": "oc-skill", "skill_file": "y"}
            ]
        },
    )


def test_opencode_installed_and_parsed():
    facts = _opencode_facts(fixture_text("opencode", "opencode.json"))
    res = get_adapter("opencode").run(facts)

    assert res.detection.status == "installed"
    assert res.detection.version == "1.20.0"
    assert res.detection.install_method is None  # unclassified location -> unknown

    cfg = res.config
    assert cfg.parse_status == "parsed"
    assert cfg.model == "fixture-provider/fixture-model"
    assert cfg.model_provider == "fixture-provider"
    assert cfg.base_url == "https://fixture-opencode.example.com/v1"

    names = sorted(m["name"] for m in cfg.mcp_servers)
    assert names == ["fixture-mcp", "fixture-remote"]
    assert cfg.settings["agents"] == ["build", "plan"]
    assert [s["name"] for s in cfg.skills] == ["oc-skill"]

    assert_clean(res.config.sanitized)
    blob = json.dumps(res.config.sanitized)
    assert "sk-fixture-opencode-key" not in blob
    # the nested apiKey entry is dropped, the harmless npm package name survives
    assert "@ai-sdk/openai-compatible" in blob


def test_opencode_no_config_files():
    facts = _opencode_facts(None)
    res = get_adapter("opencode").run(facts)
    assert res.config.parse_status == "empty"


def test_opencode_jsonc_is_parsed():
    jsonc = '{ // comment\n "model": "p/m", /* block */ "theme": "dark", }'
    facts = make_facts(
        files={"~/.config/opencode/opencode.jsonc": ("ok", jsonc)},
        probed={"opencode:opencode"},
    )
    cfg = get_adapter("opencode").run(facts).config
    assert cfg.parse_status == "parsed"
    assert cfg.model == "p/m"


# ---------------------------------------------------------------------------
# Pi
# ---------------------------------------------------------------------------


def test_pi_installed_and_parsed():
    facts = make_facts(
        files={
            "~/.pi/agent/settings.json": ("ok", fixture_text("pi", "settings.json")),
            "~/.pi/agent/models.json": ("ok", fixture_text("pi", "models.json")),
        },
        which={"pi:pi": "/usr/local/bin/pi"},
        versions={"pi:pi": (0, "1.2.3")},
        probed={"pi:pi"},
    )
    res = get_adapter("pi").run(facts)

    assert res.detection.status == "installed"
    assert res.detection.version == "1.2.3"
    cfg = res.config
    assert cfg.parse_status == "parsed"
    assert cfg.model == "fixture-model-v1"
    assert cfg.model_provider == "fixture-provider"
    assert cfg.base_url == "https://fixture-pi.example.com/v1"
    assert cfg.settings["providers"] == ["fixture-provider", "other-provider"]
    assert cfg.settings["declared_models"] == 2
    assert any("auth.json is never read" in w for w in cfg.warnings)
    assert_clean(res.config.sanitized)


def test_pi_missing_settings():
    facts = make_facts(
        files={"~/.pi/agent/settings.json": None, "~/.pi/agent/models.json": None},
        probed={"pi:pi"},
    )
    res = get_adapter("pi").run(facts)
    assert res.detection.status == "not_installed"
    assert res.config.parse_status == "empty"


# ---------------------------------------------------------------------------
# Hermes
# ---------------------------------------------------------------------------


def test_hermes_installed_and_parsed():
    facts = make_facts(
        files={"~/.hermes/config.yaml": ("ok", fixture_text("hermes", "config.yaml"))},
        which={"hermes:hermes": "/home/fixture/.local/bin/hermes"},
        versions={"hermes:hermes": (0, "Hermes Agent v1.2.3 (2026.1.1) stable local abc")},
        probed={"hermes:hermes"},
    )
    res = get_adapter("hermes").run(facts)

    assert res.detection.status == "installed"
    assert res.detection.version == "1.2.3"
    assert res.detection.install_method == "user-local install"
    cfg = res.config
    assert cfg.parse_status == "parsed"
    assert cfg.model == "fixture-model-v1"
    assert cfg.model_provider == "fixture-provider"
    assert cfg.base_url == "https://fixture-hermes.example.com/v1"
    assert cfg.settings["key_env"] == "FIXTURE_HERMES_API_KEY"
    assert cfg.settings["providers"] == ["fixture-provider", "inline-provider"]
    assert cfg.settings["toolsets"] == ["core", "web"]
    assert any("inline api_key" in w for w in cfg.warnings)
    assert any(".env is never read" in w for w in cfg.warnings)

    names = [m["name"] for m in cfg.mcp_servers]
    assert names == ["fixture-hermes-mcp"]
    assert cfg.mcp_servers[0]["env_keys"] == ["HERMES_TOKEN"]
    assert_clean(res.config.sanitized)


def test_hermes_broken_yaml():
    facts = make_facts(
        files={"~/.hermes/config.yaml": ("ok", fixture_text("hermes", "broken.yaml"))},
        which={"hermes:hermes": "/usr/bin/hermes"},
        probed={"hermes:hermes"},
    )
    res = get_adapter("hermes").run(facts)
    assert res.config.parse_status == "error"


# ---------------------------------------------------------------------------
# Registry / contract
# ---------------------------------------------------------------------------


def test_every_adapter_declares_itself():
    ids = [a.agent_id for a in all_adapters()]
    assert set(ids) >= {"codex", "claude", "opencode", "pi", "hermes"}
    for a in all_adapters():
        assert a.probe_spec() is not None


def test_adapters_survive_empty_facts():
    for a in all_adapters():
        res = a.run(RemoteFacts())
        assert res.agent_id == a.agent_id
        assert res.detection.status in ("unknown", "not_installed")


@pytest.mark.parametrize(
    "agent_id,fixture",
    [
        ("codex", ("codex", "config.toml")),
        ("opencode", ("opencode", "opencode.json")),
        ("pi", ("pi", "settings.json")),
        ("hermes", ("hermes", "config.yaml")),
        ("claude", ("claude", "settings.json")),
    ],
)
def test_fixture_configs_never_leak(agent_id, fixture):
    """Every fixture holds a fake credential; none of them may come back out."""
    text = fixture_text(*fixture)
    facts = make_facts(
        files={f"~/.{agent_id}/whatever": ("ok", text)},
        probed={f"{agent_id}:{agent_id}"},
    )
    res = get_adapter(agent_id).run(facts)
    assert_clean(res.config.sanitized)


def test_claude_unreadable_user_settings_does_not_count_as_parsed():
    """~/.claude.json being readable must not mask a broken settings.json.

    persist keeps the last good config only for empty/error/unsupported, so a
    "partial" verdict here is what prevents a near-empty merge from replacing
    model/provider/base_url.
    """
    facts = _claude_facts(None, fixture_text("claude", "global.json"))
    facts.files["~/.claude/settings.json"] = FileFact(
        path="~/.claude/settings.json", status="unreadable"
    )
    cfg = get_adapter("claude").run(facts).config
    assert cfg.model is None
    assert cfg.mcp_servers  # ~/.claude.json was readable
    assert cfg.parse_status == "partial"
    assert cfg.resolution == "declared"
