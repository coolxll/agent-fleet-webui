"""Qoder adapter tests driven by fixtures."""

from __future__ import annotations

import json

from agent_fleet.adapters.registry import get_adapter
from helpers import fixture_text, make_facts


def _qoder_facts(settings=None, mcp=None, with_binary=True, bin_name="qodercli", skills=None):
    bin_key = f"qoder:{bin_name}"
    files = {
        "~/.qoder/settings.json": (
            "ok",
            settings if settings is not None else fixture_text("qoder", "settings.json"),
        ),
        "~/.qoder/mcp.json": (
            "ok",
            mcp if mcp is not None else fixture_text("qoder", "mcp.json"),
        ),
        "~/.qoder/settings.local.json": None,
        "~/.qoder/AGENTS.md": None,
        "~/.qoder-cn/settings.json": None,
        "~/.qoder-cn/mcp.json": None,
        "~/.qoder-cn/settings.local.json": None,
        "~/.qoder-cn/AGENTS.md": None,
    }
    return make_facts(
        files=files,
        which={bin_key: f"/Users/fixture/.local/bin/{bin_name}"} if with_binary else {},
        versions={bin_key: (0, "1.1.73")},
        probed={"qoder:qodercli", "qoder:qoderclicn"},
        skills=skills
        or {
            "~/.qoder/skills": [
                {"name": "qoder-skill", "path": "qoder-skill", "skill_file": "x"}
            ]
        },
        ls={
            "~/.qoder/plugins": [("qoder-plugin-a", "d"), ("qoder-plugin-b", "d")],
            "~/.qoder/rules": [("coding-standards.md", "f")],
        },
    )


def test_qoder_installed_and_parsed():
    res = get_adapter("qoder").run(_qoder_facts())

    assert res.detection.status == "installed"
    assert res.detection.detected is True
    assert res.detection.version == "1.1.73"
    assert res.detection.exec_path == "/Users/fixture/.local/bin/qodercli"
    assert res.detection.install_method == "user-local install"
    assert res.detection.config_dir == "~/.qoder"

    cfg = res.config
    assert cfg.parse_status == "parsed"
    assert cfg.model == "qwen-coder-plus"
    assert cfg.model_provider == "qoder"
    assert cfg.settings["language"] == "zh-CN"
    assert cfg.settings["autoMemoryEnabled"] is True
    assert cfg.settings["trustedDirectories"] == 2
    assert cfg.settings["hooks"] == ["PreToolUse", "Stop"]
    assert cfg.settings["plugins"] == ["qoder-plugin-a", "qoder-plugin-b"]
    assert cfg.settings["rules"] == ["coding-standards.md"]
    assert "unknownPrivateSetting" in cfg.unsupported_fields

    # MCP servers from settings.json and mcp.json
    by_name = {m["name"]: m for m in cfg.mcp_servers}
    assert "fixture-user-mcp" in by_name
    assert "fixture-sse-mcp" in by_name

    user_mcp = by_name["fixture-user-mcp"]
    assert user_mcp["transport"] == "stdio"
    assert user_mcp["command"] == "npx"
    assert user_mcp["env_keys"] == ["FIXTURE_KEY"]
    assert user_mcp["scope"] == "user"

    sse_mcp = by_name["fixture-sse-mcp"]
    assert sse_mcp["transport"] == "sse"
    assert sse_mcp["url"] == "https://mcp.fixture.com/sse"
    assert sse_mcp["url_masked"] is True

    # Skills
    assert len(cfg.skills) == 1
    assert cfg.skills[0]["name"] == "qoder-skill"


def test_qoder_qoderclicn_detection_and_cn_config():
    facts = make_facts(
        files={
            "~/.qoder/settings.json": None,
            "~/.qoder-cn/settings.json": (
                "ok",
                json.dumps({"model": "qwen-max", "language": "zh-CN"}),
            ),
        },
        which={"qoder:qoderclicn": "/usr/bin/qoderclicn"},
        versions={"qoder:qoderclicn": (0, "1.2.0")},
        probed={"qoder:qodercli", "qoder:qoderclicn"},
        skills={
            "~/.qoder-cn/skills": [
                {"name": "cn-skill", "path": "cn-skill", "skill_file": "x"}
            ]
        },
        ls={
            "~/.qoder-cn/plugins": [("cn-plugin", "d")],
            "~/.qoder-cn/rules": [("cn-rule.md", "f")],
        },
    )
    res = get_adapter("qoder").run(facts)
    assert res.detection.status == "installed"
    assert res.detection.exec_path == "/usr/bin/qoderclicn"
    assert res.detection.version == "1.2.0"
    assert res.detection.config_dir == "~/.qoder-cn"
    assert res.config.sanitized["config_file"] == "~/.qoder-cn/settings.json"
    assert res.config.model == "qwen-max"
    assert res.config.settings["language"] == "zh-CN"
    assert res.config.settings["plugins"] == ["cn-plugin"]
    assert res.config.settings["rules"] == ["cn-rule.md"]
    assert len(res.config.skills) == 1
    assert res.config.skills[0]["name"] == "cn-skill"


def test_qoder_never_leaks_credentials():
    res = get_adapter("qoder").run(_qoder_facts())
    blob = json.dumps(res.config.sanitized)
    assert "sk-fixture" not in blob
    assert "abcdef012345" not in blob
    assert "apiKey" in res.config.unsupported_fields


def test_qoder_custom_endpoint_and_provider():
    custom_settings = json.dumps(
        {
            "model": "deepseek-v3",
            "provider": "custom-openai",
            "baseURL": "https://custom-api.example.com/v1?token=sk-fixture-secret",
        }
    )
    facts = _qoder_facts(settings=custom_settings, mcp="{}")
    cfg = get_adapter("qoder").run(facts).config
    assert cfg.model == "deepseek-v3"
    assert cfg.model_provider == "custom-openai"
    assert cfg.base_url == "https://custom-api.example.com/v1"
    assert cfg.base_url_masked is True


def test_qoder_project_mcp_servers_are_discovered():
    project_mcp = json.dumps(
        {"mcpServers": {"project-tool": {"command": "tool-cmd", "args": ["run"]}}}
    )
    facts = make_facts(
        files={
            "~/.qoder/settings.json": ("ok", fixture_text("qoder", "settings.json")),
            "~/.qoder/mcp.json": None,
            "~/.qoder/settings.local.json": None,
            "~/.qoder/AGENTS.md": None,
            "/Users/fixture/work/a/.mcp.json": ("ok", project_mcp),
        },
        which={"qoder:qodercli": "/usr/local/bin/qodercli"},
        versions={"qoder:qodercli": (0, "1.1.73")},
        probed={"qoder:qodercli", "qoder:qoderclicn"},
    )
    cfg = get_adapter("qoder").run(facts).config
    server = next(m for m in cfg.mcp_servers if m["name"] == "project-tool")
    assert server["scope"] == "project"
    assert server["source"] == "/Users/fixture/work/a/.mcp.json"
    assert server["command"] == "tool-cmd"


def test_qoder_absent_binary():
    facts = _qoder_facts(with_binary=False)
    res = get_adapter("qoder").run(facts)
    assert res.detection.status == "not_installed"


def test_qoder_never_probed_is_unknown():
    facts = make_facts(files={"~/.qoder/settings.json": ("ok", "{}")})
    res = get_adapter("qoder").run(facts)
    assert res.detection.status == "unknown"


def test_qoder_broken_settings_json():
    facts = _qoder_facts(settings="{ invalid json")
    res = get_adapter("qoder").run(facts)
    assert res.config.parse_status in ("empty", "partial", "error")
