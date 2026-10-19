"""Remote operational actions (upgrade, repair) with strict injection prevention.

Zero client-provided commands are executed. All commands are synthesized from a
strict server-side whitelist and parameterless templates, run over the established
Runner pipeline with PATH_BOOTSTRAP, and protected by per-runtime concurrency locks.

Supports both synchronous execution and background task queues with polling.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.remotes.probe import PATH_BOOTSTRAP
from agent_fleet.services.persist import persist_result
from agent_fleet.services.scanner import build_runner, scan_runtime, to_ref
from agent_fleet.settings import get_settings

UPGRADE_RULES: dict[str, dict[str, str]] = {
    "codex": {"package": "@openai/codex", "type": "npm"},
    "claude": {"package": "@anthropic-ai/claude-code", "type": "npm_or_cli"},
    "opencode": {"package": "opencode-ai", "type": "npm_or_cli"},
    "pi": {"package": "@earendil-works/pi-coding-agent", "type": "npm_or_cli"},
    "codebuddy": {"package": "@tencent-ai/codebuddy-code", "type": "npm"},
    "qoder": {"package": "@qoder-ai/qodercli", "type": "npm_or_cli"},
    "agy": {"package": "antigravity-cli", "type": "agy"},
    "hermes": {"package": "hermes-agent", "type": "hermes"},
}

# Official self-updaters. When present they are tried first and the package
# manager is the fallback, so a working vendor updater never needs a full
# reinstall and a broken one still recovers.
OFFICIAL_UPDATERS: dict[str, tuple[str, str]] = {
    "claude": ("claude", "update"),
    "qoder": ("qodercli", "update"),
    "opencode": ("opencode", "upgrade"),
    "pi": ("pi", "update"),
}

# Hermes ships its own updater; the upstream install script is the fallback
# (cc-switch uses the same chain). PyPI is NOT a safe fallback: hermes-agent
# stopped publishing there at 0.19.0 while GitHub releases moved to 0.22.x.
HERMES_INSTALL_URL = (
    "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
)

WIN_PATH_BOOTSTRAP = """$ProgressPreference = "SilentlyContinue"
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$paths = @(
  "$HOME\\AppData\\Roaming\\npm",
  "$HOME\\AppData\\Local\\Programs\\node",
  "$HOME\\AppData\\Local\\pnpm",
  "$HOME\\scoop\\shims",
  "$HOME\\.cargo\\bin",
  "$HOME\\.local\\bin",
  "$HOME\\.opencode\\bin",
  "$HOME\\AppData\\Local\\agy\\bin",
  "C:\\Program Files\\nodejs"
)
foreach ($p in $paths) {
  if ((Test-Path -LiteralPath $p) -and ($env:PATH -notlike "*$p*")) {
    $env:PATH = "$p;$env:PATH"
  }
}
"""

_host_locks: dict[str, asyncio.Lock] = {}
_tasks: dict[str, dict[str, Any]] = {}
_background_tasks: set[asyncio.Task] = set()


def _get_lock(runtime_id: str) -> asyncio.Lock:
    if runtime_id not in _host_locks:
        _host_locks[runtime_id] = asyncio.Lock()
    return _host_locks[runtime_id]


def build_upgrade_script(agent_id: str, platform: str, channel: str | None = None) -> str:
    rule = UPGRADE_RULES.get(agent_id)
    if not rule:
        raise ValueError(f"Agent '{agent_id}' does not support automated upgrades")

    pkg = rule["package"]
    rule_type = rule["type"]
    tag = "stable" if channel == "stable" else "latest"

    if platform == "windows":
        if rule_type == "npm":
            body = (
                f"if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {{\n"
                f"  & npm.cmd install -g {pkg}@{tag}\n"
                f"}} elseif (Get-Command npm -ErrorAction SilentlyContinue) {{\n"
                f"  & npm install -g {pkg}@{tag}\n"
                f"}} else {{\n"
                f'  Write-Error "npm executable not found on Windows target"\n'
                f"  exit 127\n"
                f"}}\n"
                f"exit $LASTEXITCODE"
            )
        elif rule_type == "npm_or_cli":
            cli, sub = OFFICIAL_UPDATERS.get(
                agent_id,
                (agent_id, "update"),
            )
            body = (
                f"if (Get-Command {cli} -ErrorAction SilentlyContinue) {{\n"
                f"  & {cli} {sub}\n"
                f"  if ($LASTEXITCODE -ne 0) {{\n"
                f"    if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {{\n"
                f"      & npm.cmd install -g {pkg}@{tag}\n"
                f"    }} else {{\n"
                f"      & npm install -g {pkg}@{tag}\n"
                f"    }}\n"
                f"  }}\n"
                f"}} elseif (Get-Command npm.cmd -ErrorAction SilentlyContinue) {{\n"
                f"  & npm.cmd install -g {pkg}@{tag}\n"
                f"}} else {{\n"
                f"  & npm install -g {pkg}@{tag}\n"
                f"}}\n"
                f"exit $LASTEXITCODE"
            )
        elif rule_type == "agy":
            body = (
                f"if (Get-Command agy.cmd -ErrorAction SilentlyContinue) {{\n"
                f"  & agy.cmd update\n"
                f"}} elseif (Get-Command agy -ErrorAction SilentlyContinue) {{\n"
                f"  & agy update\n"
                f"}} else {{\n"
                f'  Write-Error "agy executable not found on Windows target"\n'
                f"  exit 127\n"
                f"}}\n"
                f"exit $LASTEXITCODE"
            )
        elif rule_type == "hermes":
            # pip install hermes-agent would pin to 0.19.0 (PyPI is stale);
            # try the official updater, then the upstream installer.
            upstream = (
                "irm " + HERMES_INSTALL_URL + " | iex"
            )
            body = (
                f"if (Get-Command hermes -ErrorAction SilentlyContinue) {{\n"
                f"  & hermes update\n"
                f"}}\n"
                f"if ($LASTEXITCODE -ne 0) {{\n"
                f"  {upstream}\n"
                f"}}\n"
                f"exit $LASTEXITCODE"
            )
        else:
            body = f"& npm.cmd install -g {pkg}@{tag}\nexit $LASTEXITCODE"

        return f"{WIN_PATH_BOOTSTRAP}\n{body}\n"

    # POSIX / WSL / macOS / Linux
    if rule_type == "npm":
        cmd = f"npm install -g {pkg}@{tag}"
    elif rule_type == "npm_or_cli":
        official = OFFICIAL_UPDATERS.get(agent_id)
        if official:
            cli, sub = official
            cmd = f"{cli} {sub} 2>&1 || npm install -g {pkg}@{tag}"
        else:
            cmd = f"npm install -g {pkg}@{tag}"
    elif rule_type == "agy":
        cmd = "agy update 2>&1"
    elif rule_type == "hermes":
        # Official updater first (git pull + reinstall, restarts the gateway);
        # upstream install script as the fallback. Never pip: PyPI is stale.
        cmd = (
            'hermes update || '
            "bash -c 'tmp=$(mktemp) && curl -fsSL " + HERMES_INSTALL_URL + " -o $tmp"
            " && bash $tmp; status=$?; rm -f $tmp; exit $status'"
        )
    else:
        cmd = f"npm install -g {pkg}@{tag}"

    # Strict PATH_BOOTSTRAP prefix guarantees npm, node, brew, nvm are available
    return f"{PATH_BOOTSTRAP}\n{cmd}\n"


async def upgrade_agent_installation(inst_id: str) -> dict[str, Any]:
    """Synchronous execution of upgrade (waits until completion)."""
    settings = get_settings()

    # 1. Fetch runtime & agent metadata from DB (short-lived transaction)
    with session_scope() as s:
        inst = s.get(models.AgentInstallation, inst_id)
        if not inst:
            raise ValueError(f"Installation '{inst_id}' not found")
        rt = inst.runtime
        if not rt:
            raise ValueError(f"Runtime for installation '{inst_id}' not found")

        cfg = getattr(inst, "config", None)
        channel = cfg.settings.get("channel") or cfg.settings.get("autoUpdatesChannel") if cfg and isinstance(cfg.settings, dict) else None
        runtime_id = rt.id
        agent_id = inst.agent_id
        old_version = inst.version
        rt_ref = to_ref(rt)
        platform = "windows" if rt.transport == "windows" else "posix"
        host_name = rt.host.name if rt.host else rt.ssh_alias or "local"

    # 2. Safety validation: agent_id must be in whitelist
    if agent_id not in UPGRADE_RULES:
        raise ValueError(f"Agent '{agent_id}' is not in the allowed upgrade whitelist")

    # 3. Generate static server-side script
    script = build_upgrade_script(agent_id, platform, channel=channel)

    # 4. Acquire per-runtime concurrency lock
    lock = _get_lock(runtime_id)
    async with lock:
        runner = build_runner(rt_ref, settings)
        start_t = time.monotonic()

        # Hard timeout for upgrade: 180 seconds
        timeout = max(180, settings.command_timeout * 3)
        res = await runner.run_script(script, timeout=timeout)
        duration_ms = int((time.monotonic() - start_t) * 1000)

        new_version = old_version
        scan_ok = False

        # 5. If command succeeded, trigger a re-scan and persist to DB
        if res.ok:
            try:
                scan_res = await scan_runtime(rt_ref, settings)
                persist_result(runtime_id, scan_res, None, settings)
                scan_ok = scan_res.ok

                # Re-query updated version
                with session_scope() as s:
                    updated_inst = s.get(models.AgentInstallation, inst_id)
                    if updated_inst:
                        new_version = updated_inst.version
            except Exception as scan_err:
                res.stderr = (res.stderr or "") + f"\n[Post-upgrade scan warning: {scan_err}]"

        return {
            "ok": res.ok,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "error": res.failure_reason(),
            "duration_ms": duration_ms,
            "agent": agent_id,
            "runtime_id": runtime_id,
            "installation_id": inst_id,
            "host": host_name,
            "old_version": old_version,
            "new_version": new_version,
            "scan_refreshed": scan_ok,
        }


# --------------------------------------------------------------------------
# Background Task Queue
# --------------------------------------------------------------------------

def get_upgrade_task(task_id: str) -> dict[str, Any] | None:
    return _tasks.get(task_id)


def get_active_task_for_installation(inst_id: str) -> dict[str, Any] | None:
    # First check running tasks
    for t in _tasks.values():
        if t.get("installation_id") == inst_id and t.get("status") == "running":
            return t
    # Fallback: check tasks finished in the last 60 seconds
    now = time.time()
    for t in sorted(_tasks.values(), key=lambda x: x.get("started_at", 0), reverse=True):
        if t.get("installation_id") == inst_id and (now - t.get("finished_at", 0) < 60):
            return t
    return None


def list_upgrade_tasks(limit: int = 30) -> list[dict[str, Any]]:
    return sorted(_tasks.values(), key=lambda x: x.get("started_at", 0), reverse=True)[:limit]


def start_upgrade_task(inst_id: str) -> dict[str, Any]:
    """Enqueue upgrade as an asynchronous background task. Returns immediately."""
    # Check if a task is already running for this installation
    active = get_active_task_for_installation(inst_id)
    if active and active.get("status") == "running":
        return active

    settings = get_settings()

    with session_scope() as s:
        inst = s.get(models.AgentInstallation, inst_id)
        if not inst:
            raise ValueError(f"Installation '{inst_id}' not found")
        rt = inst.runtime
        if not rt:
            raise ValueError(f"Runtime for installation '{inst_id}' not found")

        cfg = getattr(inst, "config", None)
        channel = cfg.settings.get("channel") or cfg.settings.get("autoUpdatesChannel") if cfg and isinstance(cfg.settings, dict) else None
        runtime_id = rt.id
        agent_id = inst.agent_id
        old_version = inst.version
        rt_ref = to_ref(rt)
        platform = "windows" if rt.transport == "windows" else "posix"
        host_name = rt.host.name if rt.host else rt.ssh_alias or "local"

    if agent_id not in UPGRADE_RULES:
        raise ValueError(f"Agent '{agent_id}' is not in the allowed upgrade whitelist")

    task_id = models.new_id()
    task_entry: dict[str, Any] = {
        "task_id": task_id,
        "installation_id": inst_id,
        "runtime_id": runtime_id,
        "agent": agent_id,
        "channel": channel,
        "host": host_name,
        "status": "running",
        "started_at": time.time(),
        "finished_at": None,
        "duration_ms": 0,
        "ok": None,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "error": None,
        "old_version": old_version,
        "new_version": None,
        "scan_refreshed": False,
    }
    _tasks[task_id] = task_entry

    bg_task = asyncio.create_task(
        _run_background_upgrade(task_id, inst_id, rt_ref, agent_id, platform, runtime_id, old_version, settings, channel=channel)
    )
    _background_tasks.add(bg_task)
    bg_task.add_done_callback(_background_tasks.discard)

    return task_entry


async def _run_background_upgrade(
    task_id: str,
    inst_id: str,
    rt_ref,
    agent_id: str,
    platform: str,
    runtime_id: str,
    old_version: str | None,
    settings,
    channel: str | None = None,
):
    entry = _tasks.get(task_id)
    if not entry:
        return

    script = build_upgrade_script(agent_id, platform, channel=channel)
    lock = _get_lock(runtime_id)

    async with lock:
        runner = build_runner(rt_ref, settings)
        start_t = time.monotonic()
        timeout = max(180, settings.command_timeout * 3)

        try:
            res = await runner.run_script(script, timeout=timeout)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start_t) * 1000)
            entry.update({
                "status": "failed",
                "ok": False,
                "exit_code": -1,
                "error": f"Execution error: {exc}",
                "duration_ms": duration_ms,
                "finished_at": time.time(),
            })
            return

        duration_ms = int((time.monotonic() - start_t) * 1000)
        new_version = old_version
        scan_ok = False

        if res.ok:
            try:
                scan_res = await scan_runtime(rt_ref, settings)
                persist_result(runtime_id, scan_res, None, settings)
                scan_ok = scan_res.ok

                with session_scope() as s:
                    updated_inst = s.get(models.AgentInstallation, inst_id)
                    if updated_inst:
                        new_version = updated_inst.version
            except Exception as scan_err:
                res.stderr = (res.stderr or "") + f"\n[Post-upgrade scan warning: {scan_err}]"

        entry.update({
            "status": "completed" if res.ok else "failed",
            "ok": res.ok,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "error": res.failure_reason(),
            "duration_ms": duration_ms,
            "new_version": new_version,
            "scan_refreshed": scan_ok,
            "finished_at": time.time(),
        })


def find_outdated_installations(
    *,
    host_id: str | None = None,
    agent_id: str | None = None,
) -> list[str]:
    """Find installation IDs that are currently detected and behind the latest available version."""
    from sqlalchemy import select
    from agent_fleet.services.updates import effective_latest, latest_available, version_state

    with session_scope() as s:
        stmt = (
            select(models.AgentInstallation)
            .join(models.Runtime)
            .where(
                models.AgentInstallation.detected.is_(True),
                models.Runtime.enabled.is_(True),
            )
        )
        if host_id:
            stmt = stmt.where(models.Runtime.host_id == host_id)
        if agent_id:
            stmt = stmt.where(models.AgentInstallation.agent_id == agent_id)

        insts = list(s.scalars(stmt).all())

        outdated_ids: list[str] = []
        for inst in insts:
            if inst.agent_id not in UPGRADE_RULES:
                continue
            cfg = getattr(inst, "config", None)
            channel = None
            if cfg and isinstance(cfg.settings, dict):
                channel = cfg.settings.get("channel") or cfg.settings.get("autoUpdatesChannel")
            update_info = latest_available(inst.agent_id)
            effective = effective_latest(inst.version, update_info, channel=channel)
            if effective and version_state(inst.version, effective) == "behind":
                outdated_ids.append(inst.id)

        return outdated_ids


def start_batch_upgrade(inst_ids: list[str]) -> list[dict[str, Any]]:
    """Enqueue a batch of installations for background upgrade."""
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for inst_id in inst_ids:
        if not inst_id or inst_id in seen:
            continue
        seen.add(inst_id)
        try:
            task = start_upgrade_task(inst_id)
            tasks.append(task)
        except Exception:
            # Skip invalid or unsupported entries without halting batch
            pass
    return tasks
