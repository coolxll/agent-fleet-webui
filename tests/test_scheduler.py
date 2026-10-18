"""Scheduled scanning: persistence, firing, and not double-running."""

from __future__ import annotations

import asyncio

import pytest

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services.scheduler import Schedule


@pytest.fixture()
def schedule(fleet_db):
    return Schedule()


def _add_runtime(settings, name="sched-box"):
    from test_scan import _make_runtime

    return _make_runtime(settings, name=name)


def test_defaults(schedule):
    assert schedule.enabled is False
    assert schedule.interval_seconds == 3600
    assert schedule.status()["running"] is False


def test_persistence_roundtrip(schedule):
    schedule.update(enabled=True, interval_seconds=1800)
    reloaded = Schedule()
    assert reloaded.enabled is True
    assert reloaded.interval_seconds == 1800
    reloaded.update(enabled=False, interval_seconds=3600)
    assert Schedule().enabled is False


def test_interval_floor(schedule):
    schedule.update(interval_seconds=1)
    assert schedule.interval_seconds == 60


def test_run_once_scans_enabled_runtimes(schedule, fleet_db, monkeypatch):
    a = _add_runtime(fleet_db, "sched-a")
    b = _add_runtime(fleet_db, "sched-b")
    with session_scope() as s:
        s.get(models.Runtime, a).enabled = True
        s.get(models.Runtime, b).enabled = True
        s.commit()

    calls = []

    async def fake_start_scan(ids, triggered_by, settings):
        calls.append((ids, triggered_by))
        return "run-fake"

    monkeypatch.setattr(
        "agent_fleet.services.scheduler.scan_service.start_scan", fake_start_scan
    )
    schedule.enabled = True
    asyncio.run(schedule._run_once())

    assert len(calls) == 1
    assert sorted(calls[0][0]) == sorted([a, b])
    assert calls[0][1] == "schedule"
    assert schedule.status()["last_run_id"] == "run-fake"
    assert schedule.status()["last_run_at"]


def test_disabled_schedule_does_not_scan(schedule, fleet_db, monkeypatch):
    """A disabled schedule must not scan, even if a run is requested."""
    _add_runtime(fleet_db, "sched-c")
    calls = []

    async def fake_start_scan(ids, triggered_by, settings):
        calls.append(ids)
        return "run-x"

    monkeypatch.setattr(
        "agent_fleet.services.scheduler.scan_service.start_scan", fake_start_scan
    )
    schedule.enabled = False
    asyncio.run(schedule._run_once())
    assert calls == []


def test_skips_when_a_run_is_active(schedule, fleet_db, monkeypatch):
    _add_runtime(fleet_db, "sched-d")
    schedule.enabled = True
    monkeypatch.setattr(
        "agent_fleet.services.scheduler.scan_service.active_runs",
        lambda: [{"run_id": "busy"}],
    )
    calls = []

    async def fake_start_scan(ids, triggered_by, settings):
        calls.append(ids)
        return "run-y"

    monkeypatch.setattr(
        "agent_fleet.services.scheduler.scan_service.start_scan", fake_start_scan
    )
    asyncio.run(schedule._run_once())
    assert calls == []
    assert schedule.status()["skipped_because_busy"] == 1


def test_loop_fires_and_stops(schedule, fleet_db, monkeypatch):
    _add_runtime(fleet_db, "sched-e")
    calls = []

    async def fake_start_scan(ids, triggered_by, settings):
        calls.append(ids)
        return "run-z"

    monkeypatch.setattr(
        "agent_fleet.services.scheduler.scan_service.start_scan", fake_start_scan
    )
    schedule.enabled = True
    schedule.interval_seconds = 0.05

    async def main():
        task = asyncio.create_task(schedule._loop())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
    assert len(calls) >= 1
    assert schedule.status()["running"] is False


def test_no_runtimes_means_no_run(schedule, fleet_db, monkeypatch):
    calls = []

    async def fake_start_scan(ids, triggered_by, settings):
        calls.append(ids)
        return "run-n"

    monkeypatch.setattr(
        "agent_fleet.services.scheduler.scan_service.start_scan", fake_start_scan
    )
    asyncio.run(schedule._run_once())
    assert calls == []


def test_interval_change_rearms_the_sleeping_loop(schedule, fleet_db, monkeypatch):
    """A shorter interval whose deadline has already passed fires promptly;
    status() reports the armed deadline, not the old one."""
    _add_runtime(fleet_db, "sched-f")
    calls = []

    async def fake_start_scan(ids, triggered_by, settings):
        calls.append(ids)
        return "run-arm"

    monkeypatch.setattr(
        "agent_fleet.services.scheduler.scan_service.start_scan", fake_start_scan
    )

    async def main():
        from datetime import datetime, timedelta

        schedule.enabled = True
        schedule.interval_seconds = 24 * 3600
        schedule._last_run_at = datetime.utcnow() - timedelta(minutes=30)
        schedule.start()
        task = schedule._task
        await asyncio.sleep(0.05)
        assert calls == []  # armed for 24h
        schedule.update(enabled=True, interval_seconds=600)  # due 20 min ago
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
    assert len(calls) == 1


def test_status_reports_the_armed_deadline(schedule):
    """next_run_at must describe what the loop is actually sleeping until."""
    from datetime import datetime, timedelta

    async def main():
        schedule.enabled = True
        schedule.interval_seconds = 3600
        schedule._last_run_at = datetime.utcnow() - timedelta(minutes=5)
        schedule.start()
        task = schedule._task
        await asyncio.sleep(0.02)
        before = schedule.status()["next_run_at"]
        schedule.update(enabled=True, interval_seconds=120)
        after = schedule.status()["next_run_at"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return before, after

    before, after = asyncio.run(main())
    assert before and after
    assert after < before  # the 2-minute deadline replaces the hourly one


def test_enabling_starts_a_run_without_waiting_a_full_interval(schedule, fleet_db, monkeypatch):
    _add_runtime(fleet_db, "sched-g")
    calls = []

    async def fake_start_scan(ids, triggered_by, settings):
        calls.append(ids)
        return "run-now"

    monkeypatch.setattr(
        "agent_fleet.services.scheduler.scan_service.start_scan", fake_start_scan
    )

    async def main():
        schedule.start()
        task = schedule._task
        await asyncio.sleep(0.02)
        schedule.update(enabled=True, interval_seconds=3600)
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
    assert len(calls) == 1


def test_update_can_be_called_without_a_running_loop(fleet_db):
    """The API calls update() from a sync route; there may be no loop yet."""
    s = Schedule()
    out = s.update(enabled=True, interval_seconds=600)
    assert out["enabled"] is True
    assert out["next_run_at"]
    s.update(enabled=False)
    assert s.status()["next_run_at"] is None
