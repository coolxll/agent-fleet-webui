"""Scan orchestration.

One runtime = one probe round trip (plus a fallback fingerprint when the
remote shell is not POSIX). Individual failures never abort a run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

NL = chr(10)

from agent_fleet.adapters.registry import all_adapters, merged_probe_spec
from agent_fleet.remotes.executor import (
    CommandRunner,
    ExecResult,
    LocalRunner,
    SSHRunner,
    WindowsRunner,
    WslRunner,
)
from agent_fleet.remotes.probe import build_probe_script, parse_probe_output
from agent_fleet.remotes.probe_win import build_windows_probe_script


@dataclass
class RuntimeRef:
    """Plain data view of a Runtime row, safe to use across awaits."""

    id: str
    key: str = "native"
    kind: str = "unknown"
    transport: str = "ssh"
    ssh_alias: str = None
    ssh_host: str = None
    ssh_port: int = None
    collect_user: str = None
    wsl_distro: str = None
    wsl_user: str = None
    host_name: str = ""
    enabled: bool = True

    @property
    def target(self) -> str:
        return self.ssh_alias or self.ssh_host or self.host_name or ""


def to_ref(rt) -> RuntimeRef:
    return RuntimeRef(
        id=rt.id,
        key=rt.key,
        kind=rt.kind,
        transport=rt.transport,
        ssh_alias=rt.ssh_alias,
        ssh_host=rt.ssh_host,
        ssh_port=rt.ssh_port,
        collect_user=rt.collect_user,
        wsl_distro=rt.wsl_distro,
        wsl_user=rt.wsl_user,
        host_name=rt.host.name if rt.host else "",
        enabled=rt.enabled,
    )


@dataclass
class RuntimeScanResult:
    runtime_id: str
    ok: bool = False
    error: str = None
    kind: str = "unknown"
    os_name: str = ""
    arch: str = ""
    home: str = ""
    user: str = ""
    duration_ms: int = 0
    transport: str = ""
    agents: list = field(default_factory=list)
    wsl_distros: list = field(default_factory=list)


def detect_kind(facts) -> str:
    o = (facts.os or "").lower()
    k = (facts.kernel or "").lower()
    if "microsoft" in k or "wsl" in k:
        return "wsl"
    if o.startswith("darwin"):
        return "macos"
    if o.startswith("linux"):
        return "linux"
    if "mingw" in o or "msys" in o or "cygwin" in o:
        return "windows"
    if o.startswith("windows") or "microsoft windows" in o:
        return "windows"
    return "unknown"


def build_runner(rt, settings):
    """Create the runner for a runtime (RuntimeRef or ORM row)."""
    if rt.transport == "local":
        return LocalRunner()
    if rt.transport == "wsl":
        parent = WindowsRunner(rt.target, rt.ssh_port, settings.ssh_connect_timeout)
        return WslRunner(parent, rt.wsl_distro or "", rt.wsl_user)
    if rt.transport == "windows":
        return WindowsRunner(rt.target, rt.ssh_port, settings.ssh_connect_timeout)
    return SSHRunner(rt.target, rt.ssh_port, settings.ssh_connect_timeout)


def _fallback_kind_from_error(stderr: str) -> str:
    low = (stderr or "").lower()
    if "not recognized" in low:
        return "windows"
    if "cmd.exe" in low or "powershell" in low:
        return "windows"
    return "unknown"


async def probe_runtime(rt, settings) -> RuntimeScanResult:
    """Collect facts for one runtime. Never raises."""
    rt = to_ref(rt) if not isinstance(rt, RuntimeRef) else rt
    started = time.monotonic()
    result = RuntimeScanResult(runtime_id=rt.id)
    rt = to_ref(rt) if not isinstance(rt, RuntimeRef) else rt
    windows = rt.transport == "windows"
    if windows:
        scripts = build_windows_probe_script(merged_probe_spec())
    else:
        scripts = [build_probe_script(merged_probe_spec())]
    runner = build_runner(rt, settings)
    result.transport = runner.describe()

    chunks = []
    for script in scripts:
        per_chunk = max(10, settings.command_timeout // max(1, len(scripts)))
        res = await runner.run_script(script, per_chunk)
        if res.timed_out:
            result.error = (
                f"probe timed out after {per_chunk}s via {runner.describe()}"
            )
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result
        if res.error:
            result.error = f"{res.error} via {runner.describe()}"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result
        chunks.append(res.stdout)
        if res.exit_code != 0 and not chunks[0].strip():
            break

    res = ExecResult(stdout=NL.join(chunks), stderr="", exit_code=0)
    facts = parse_probe_output(res.stdout)
    if not facts.os and res.exit_code != 0:
        guessed = _fallback_kind_from_error(res.stderr)
        result.duration_ms = int((time.monotonic() - started) * 1000)
        if guessed == "windows":
            result.kind = "windows"
            result.os_name = "Windows"
            result.error = (
                "remote shell is not POSIX; native Windows probing is not implemented yet"
            )
        else:
            result.error = (
                f"probe produced no facts (exit {res.exit_code}): {res.stderr.strip()[:200]}"
            )
        return result

    result.ok = True
    result.kind = detect_kind(facts)
    result.os_name = _os_label(facts)
    result.arch = facts.arch
    result.home = facts.home
    result.user = facts.user
    result.agents = [a.run(facts) for a in all_adapters()]
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def _os_label(facts) -> str:
    if facts.macosver:
        return "macOS " + facts.macosver
    if facts.osrelease:
        return facts.osrelease
    parts = [p for p in (facts.os, facts.kernel) if p]
    return " ".join(parts) or "unknown"


async def scan_runtime(rt, settings) -> RuntimeScanResult:
    """Probe a runtime and, for Windows hosts, discover its WSL distros."""
    result = await probe_runtime(rt, settings)
    rt = to_ref(rt) if not isinstance(rt, RuntimeRef) else rt
    if result.ok and result.kind == "windows":
        result.wsl_distros = await _discover_wsl(rt, settings)
    return result


async def _discover_wsl(rt, settings):
    parent = CommandRunner(rt.target, rt.ssh_port, settings.ssh_connect_timeout)
    res = await parent.run_command(["wsl.exe", "-l", "-q"], min(20, settings.command_timeout))
    if not res.ok or not res.stdout:
        return []
    skip = ("windows subsystem for linux",)
    names = []
    for raw in res.stdout.replace(chr(0), "").split(chr(10)):
        n = raw.strip().strip(chr(13)).strip()
        if not n or n.lower() in skip:
            continue
        # strip a trailing "(Default)" / localized marker
        for marker in ("(Default)", "(默认)"):
            if n.endswith(marker):
                n = n[: -len(marker)].strip()
        if n and n not in names:
            names.append(n)
    return names
