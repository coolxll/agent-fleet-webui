"""Scanner behaviour with fake runners: failures, timeouts, concurrency."""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.remotes.executor import ExecResult
from agent_fleet.services import persist, scan_service
from agent_fleet.services.scanner import RuntimeRef, probe_runtime, scan_runtime

TAB = chr(9)
NL = chr(10)


def _stream(agents: dict, os_name="Linux", home="/home/fixture") -> str:
    """Build a probe record stream the way a real remote would."""
    lines = [
        "AF" + TAB + "os" + TAB + os_name,
        "AF" + TAB + "arch" + TAB + "x86_64",
        "AF" + TAB + "home" + TAB + home,
        "AF" + TAB + "user" + TAB + "fixture",
    ]
    for key, spec in agents.items():
        lines.append("AF" + TAB + "probed" + TAB + key)
        if spec.get("path"):
            lines.append("AF" + TAB + "which" + TAB + key + TAB + spec["path"])
            lines.append(
                "AF" + TAB + "version" + TAB + key + TAB + "0" + TAB + spec.get("version", "")
            )
        else:
            lines.append("AF" + TAB + "version" + TAB + key + TAB + "127" + TAB + "not found")
    lines.append("AF" + TAB + "done" + TAB + "1")
    return NL.join(lines)


CODEIX_TOML = 'model = "gpt-5-fixture"' + NL + 'approval_policy = "never"'


def _codex_stream() -> str:
    body = [
        "AF" + TAB + "os" + TAB + "Linux",
        "AF" + TAB + "arch" + TAB + "x86_64",
        "AF" + TAB + "home" + TAB + "/home/fixture",
        "AF" + TAB + "user" + TAB + "fixture",
        "AF" + TAB + "probed" + TAB + "codex:codex",
        "AF" + TAB + "which" + TAB + "codex:codex" + TAB + "/usr/local/bin/codex",
        "AF" + TAB + "version" + TAB + "codex:codex" + TAB + "0" + TAB + "codex-cli 1.2.3",
        "AF" + TAB + "file" + TAB + "~/.codex/config.toml" + TAB + str(len(CODEIX_TOML)) + TAB + "present",
    ]
    body += CODEIX_TOML.split(NL)
    body += [
        "AF" + TAB + "endfile" + TAB + "~/.codex/config.toml",
        # the other four agents were probed but are absent
        "AF" + TAB + "probed" + TAB + "claude:claude",
        "AF" + TAB + "probed" + TAB + "opencode:opencode",
        "AF" + TAB + "probed" + TAB + "pi:pi",
        "AF" + TAB + "probed" + TAB + "hermes:hermes",
        "AF" + TAB + "done" + TAB + "1",
    ]
    return NL.join(body)


class FakeRunner:
    kind = "fake"

    def __init__(self, stdout="", delay=0.0, error=None, timed_out=False, exit_code=0):
        self.stdout = stdout
        self.delay = delay
        self.error = error
        self.timed_out = timed_out
        self.exit_code = exit_code

    async def _respond(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.timed_out:
            return ExecResult(exit_code=-1, timed_out=True, error="timed out after 1s")
        if self.error:
            return ExecResult(exit_code=255, error=self.error)
        return ExecResult(stdout=self.stdout, exit_code=self.exit_code)

    async def run_script(self, script, timeout):
        return await self._respond()

    async def run_command(self, argv, timeout):
        return await self._respond()

    async def run_stdin(self, argv, script, timeout):
        return await self._respond()

    def describe(self):
        return "fake"


def _make_runtime(settings, name="box-a", key="native", kind="unknown", transport="ssh"):
    with session_scope() as s:
        host = models.Host(id=models.new_id(), name=name, inventory_source="test")
        s.add(host)
        s.flush()
        rt = models.Runtime(
            id=models.new_id(),
            host_id=host.id,
            key=key,
            kind=kind,
            transport=transport,
            ssh_alias=name,
        )
        s.add(rt)
        s.flush()
        rid = rt.id
    return rid


def test_successful_probe_creates_installations(fleet_db, monkeypatch):
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner", lambda rt, s: FakeRunner(_codex_stream())
    )
    rid = _make_runtime(fleet_db)
    ref = _ref(rid)

    result = asyncio.run(scan_runtime(ref, fleet_db))
    persist.persist_result(rid, result, None, fleet_db)

    assert result.ok is True
    assert result.kind == "linux"
    with session_scope() as s:
        rt = s.get(models.Runtime, rid)
        assert rt.reachable is True
        assert rt.last_error is None
        assert sum(1 for i in rt.installations if i.detected) == 1
        insts = {i.agent_id: i for i in rt.installations}
        assert insts["codex"].status == "installed"
        assert insts["codex"].version == "1.2.3"
        assert insts["codex"].exec_path == "/usr/local/bin/codex"
        assert insts["codex"].config_status == "parsed"
        assert insts["claude"].status == "not_installed"
        assert insts["claude"].detected is False
        # config was parsed into structured fields
        assert insts["codex"].config.model == "gpt-5-fixture"
        assert insts["codex"].config.settings["approval_policy"] == "never"
        sources = {src.path: src.status for src in insts["codex"].sources}
        assert sources["~/.codex/config.toml"] == "ok"


def _ref(rid):
    with session_scope() as s:
        from agent_fleet.services.scanner import to_ref

        return to_ref(s.get(models.Runtime, rid))


def test_connection_failure_keeps_previous_data(fleet_db, monkeypatch):
    rid = _make_runtime(fleet_db)
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner", lambda rt, s: FakeRunner(_codex_stream())
    )
    good = asyncio.run(scan_runtime(_ref(rid), fleet_db))
    persist.persist_result(rid, good, None, fleet_db)

    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner",
        lambda rt, s: FakeRunner(error="ssh: connect to host box-a port 22: Operation timed out"),
    )
    bad = asyncio.run(scan_runtime(_ref(rid), fleet_db))
    persist.persist_result(rid, bad, None, fleet_db)

    assert bad.ok is False
    with session_scope() as s:
        rt = s.get(models.Runtime, rid)
        assert rt.reachable is False
        assert "timed out" in rt.last_error
        # the last known good data survives
        assert sum(1 for i in rt.installations if i.detected) == 1
        insts = {i.agent_id: i for i in rt.installations}
        assert insts["codex"].status == "installed"
        assert insts["codex"].version == "1.2.3"
        assert insts["codex"].config.model == "gpt-5-fixture"


def test_probe_timeout_is_reported(fleet_db, monkeypatch):
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner",
        lambda rt, s: FakeRunner(delay=0.3, timed_out=True),
    )
    rid = _make_runtime(fleet_db)
    monkeypatch.setattr(fleet_db.__class__, "command_timeout", 0.05, raising=False)
    result = asyncio.run(scan_runtime(_ref(rid), fleet_db))
    assert result.ok is False
    assert "timed out" in (result.error or "")


def test_empty_output_is_an_error_not_absence(fleet_db, monkeypatch):
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner",
        lambda rt, s: FakeRunner(stdout="", exit_code=255),
    )
    rid = _make_runtime(fleet_db)
    result = asyncio.run(scan_runtime(_ref(rid), fleet_db))
    assert result.ok is False
    persist.persist_result(rid, result, None, fleet_db)
    with session_scope() as s:
        rt = s.get(models.Runtime, rid)
        assert rt.reachable is False
        # a failed probe must not be recorded as "nothing installed"
        for i in rt.installations:
            assert i.status == "not_scanned"


def test_slow_host_does_not_block_others(fleet_db, monkeypatch):
    ids = [_make_runtime(fleet_db, name="b" + str(i)) for i in range(4)]

    def runner_for(rt, s):
        if rt.ssh_alias == "b0":
            return FakeRunner(_codex_stream(), delay=1.2)
        return FakeRunner(_codex_stream())

    monkeypatch.setattr("agent_fleet.services.scanner.build_runner", runner_for)
    monkeypatch.setattr(fleet_db.__class__, "scan_concurrency", 4, raising=False)

    async def main():
        t = time.monotonic()
        results = await asyncio.gather(*[scan_runtime(_ref(i), fleet_db) for i in ids])
        return time.monotonic() - t, results

    elapsed, results = asyncio.run(main())
    assert all(r.ok for r in results)
    # sequential would need >4s; concurrent stays near the slowest single host
    assert elapsed < 2.0


def test_scan_service_records_per_runtime_tasks(fleet_db, monkeypatch):
    ids = [_make_runtime(fleet_db, name="c" + str(i)) for i in range(3)]

    def runner_for(rt, s):
        if rt.ssh_alias == "c2":
            return FakeRunner(error="permission denied (publickey)")
        return FakeRunner(_codex_stream())

    monkeypatch.setattr("agent_fleet.services.scanner.build_runner", runner_for)
    monkeypatch.setattr(fleet_db.__class__, "scan_concurrency", 3, raising=False)

    async def main():
        run_id = await scan_service.start_scan(ids, "manual", fleet_db)
        for _ in range(200):
            entry = scan_service.run_status(run_id)
            if entry and entry["status"] == "done":
                break
            await asyncio.sleep(0.05)
        return run_id

    run_id = asyncio.run(main())

    with session_scope() as s:
        run = s.get(models.ScanRun, run_id)
        assert run.status == "partial"
        assert run.succeeded == 2
        assert run.failed == 1
        tasks = {t.runtime_id: t for t in s.execute(
            select(models.ScanTask).where(models.ScanTask.run_id == run_id)
        ).scalars().all()}
        assert tasks[ids[0]].status == "ok"
        assert tasks[ids[2]].status == "failed"
        assert "permission denied" in (tasks[ids[2]].error or "")
        # snapshot written for the successful runtimes only
        snaps = s.execute(select(models.Snapshot)).scalars().all()
        assert sorted(snap.runtime_id for snap in snaps) == sorted(ids[:2])


def test_config_only_when_binary_missing(fleet_db, monkeypatch):
    stream = NL.join(
        [
            "AF" + TAB + "os" + TAB + "Linux",
            "AF" + TAB + "probed" + TAB + "codex:codex",
            "AF" + TAB + "version" + TAB + "codex:codex" + TAB + "127" + TAB + "not found",
            "AF" + TAB + "file" + TAB + "~/.codex/config.toml" + TAB + str(len(CODEIX_TOML)) + TAB + "present",
        ]
        + CODEIX_TOML.split(NL)
        + ["AF" + TAB + "endfile" + TAB + "~/.codex/config.toml", "AF" + TAB + "done" + TAB + "1"]
    )
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner", lambda rt, s: FakeRunner(stream)
    )
    rid = _make_runtime(fleet_db)
    result = asyncio.run(scan_runtime(_ref(rid), fleet_db))
    persist.persist_result(rid, result, None, fleet_db)

    with session_scope() as s:
        insts = {i.agent_id: i for i in s.get(models.Runtime, rid).installations}
        # binary gone, config still on disk: not "not installed", not "installed"
        assert insts["codex"].status == "config_only"
        assert insts["codex"].detected is False
        assert insts["claude"].status == "not_installed"
