"""Tests for recovering interrupted scans on process restart (Issue #22)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services import scan_service


def _create_fixture_runtimes(host_name: str = "host-scans") -> list[str]:
    with session_scope() as s:
        host = models.Host(id=models.new_id(), name=host_name, enabled=True)
        s.add(host)
        s.flush()
        runtime_ids = []
        for i in range(5):
            rt = models.Runtime(
                id=models.new_id(),
                host_id=host.id,
                key=f"rt-{i}",
                kind="ssh",
                label=f"Runtime {i}",
                enabled=True,
            )
            s.add(rt)
            s.flush()
            runtime_ids.append(rt.id)
        s.commit()
    return runtime_ids


def test_recover_interrupted_scans_status_and_tasks(fleet_db):
    """ScanRun and ScanTasks interrupted mid-flight are recovered to terminal 'interrupted'."""
    runtime_ids = _create_fixture_runtimes("host-test-1")
    run_id = models.new_id()

    with session_scope() as s:
        run = models.ScanRun(
            id=run_id,
            status="running",
            triggered_by="manual",
            target_runtime_ids=runtime_ids,
            total=5,
            concurrency=2,
            started_at=models.utcnow(),
        )
        s.add(run)

        # Task 0: ok
        t0 = models.ScanTask(
            id=models.new_id(),
            run_id=run_id,
            runtime_id=runtime_ids[0],
            status="ok",
            agents_found=2,
            duration_ms=120,
            finished_at=models.utcnow(),
        )
        # Task 1: failed
        t1 = models.ScanTask(
            id=models.new_id(),
            run_id=run_id,
            runtime_id=runtime_ids[1],
            status="failed",
            error="Connection refused",
            duration_ms=250,
            finished_at=models.utcnow(),
        )
        # Task 2: skipped
        t2 = models.ScanTask(
            id=models.new_id(),
            run_id=run_id,
            runtime_id=runtime_ids[2],
            status="skipped",
            error="runtime disabled",
            duration_ms=0,
            finished_at=models.utcnow(),
        )
        # Task 3: running (interrupted by crash/restart)
        t3 = models.ScanTask(
            id=models.new_id(),
            run_id=run_id,
            runtime_id=runtime_ids[3],
            status="running",
            started_at=models.utcnow(),
        )
        # Task 4: pending (never picked up before crash/restart)
        t4 = models.ScanTask(
            id=models.new_id(),
            run_id=run_id,
            runtime_id=runtime_ids[4],
            status="pending",
        )
        s.add_all([t0, t1, t2, t3, t4])
        s.commit()

    # Trigger recovery
    recovered = scan_service.recover_interrupted_scans()
    assert recovered == [run_id]

    # Verify run and task states in DB
    with session_scope() as s:
        run_row = s.get(models.ScanRun, run_id)
        assert run_row is not None
        assert run_row.status == "interrupted"
        assert run_row.error == "Process restarted while scan was in progress"
        assert run_row.finished_at is not None
        assert run_row.succeeded == 1
        assert run_row.failed == 1

        tasks = {
            t.runtime_id: t
            for t in s.execute(
                select(models.ScanTask).where(models.ScanTask.run_id == run_id)
            ).scalars().all()
        }
        # Succeeded / failed / skipped tasks remain untouched
        assert tasks[runtime_ids[0]].status == "ok"
        assert tasks[runtime_ids[0]].agents_found == 2
        assert tasks[runtime_ids[0]].error is None

        assert tasks[runtime_ids[1]].status == "failed"
        assert tasks[runtime_ids[1]].error == "Connection refused"

        assert tasks[runtime_ids[2]].status == "skipped"
        assert tasks[runtime_ids[2]].error == "runtime disabled"

        # Running and pending tasks become interrupted
        assert tasks[runtime_ids[3]].status == "interrupted"
        assert tasks[runtime_ids[3]].error == "Process restarted while scan was in progress"
        assert tasks[runtime_ids[3]].finished_at is not None

        assert tasks[runtime_ids[4]].status == "interrupted"
        assert tasks[runtime_ids[4]].error == "Process restarted while scan was in progress"
        assert tasks[runtime_ids[4]].finished_at is not None

    # Idempotency check: second invocation does not alter state or re-mark
    second_run = scan_service.recover_interrupted_scans()
    assert second_run == []

    with session_scope() as s:
        run_row = s.get(models.ScanRun, run_id)
        assert run_row.status == "interrupted"
        assert run_row.succeeded == 1
        assert run_row.failed == 1


def test_recover_interrupted_preserves_completed_and_handles_pending(fleet_db):
    """Already completed scans are preserved; pending runs are interrupted."""
    runtime_ids = _create_fixture_runtimes("host-test-2")

    with session_scope() as s:
        # Completed run
        c_id = models.new_id()
        c_run = models.ScanRun(
            id=c_id,
            status="completed",
            triggered_by="manual",
            total=1,
            succeeded=1,
            failed=0,
            started_at=models.utcnow(),
            finished_at=models.utcnow(),
        )
        c_task = models.ScanTask(
            id=models.new_id(),
            run_id=c_id,
            runtime_id=runtime_ids[0],
            status="ok",
            finished_at=models.utcnow(),
        )
        s.add_all([c_run, c_task])

        # Pending run
        p_id = models.new_id()
        p_run = models.ScanRun(
            id=p_id,
            status="pending",
            triggered_by="schedule",
            total=1,
            started_at=models.utcnow(),
        )
        p_task = models.ScanTask(
            id=models.new_id(),
            run_id=p_id,
            runtime_id=runtime_ids[1],
            status="pending",
        )
        s.add_all([p_run, p_task])
        s.commit()

    recovered = scan_service.recover_interrupted_scans()
    assert recovered == [p_id]

    with session_scope() as s:
        assert s.get(models.ScanRun, c_id).status == "completed"
        assert s.get(models.ScanRun, p_id).status == "interrupted"
        assert s.get(models.ScanRun, p_id).error == "Process restarted while scan was in progress"


def test_api_scans_and_scan_detail_returns_interrupted(client, fleet_db):
    """API endpoints /scans and /scan/{id} expose interrupted stats and reason."""
    runtime_ids = _create_fixture_runtimes("host-api-test")
    run_id = models.new_id()

    with session_scope() as s:
        run = models.ScanRun(
            id=run_id,
            status="running",
            triggered_by="manual",
            target_runtime_ids=runtime_ids[:2],
            total=2,
            started_at=models.utcnow(),
        )
        t0 = models.ScanTask(
            id=models.new_id(),
            run_id=run_id,
            runtime_id=runtime_ids[0],
            status="ok",
            finished_at=models.utcnow(),
        )
        t1 = models.ScanTask(
            id=models.new_id(),
            run_id=run_id,
            runtime_id=runtime_ids[1],
            status="running",
        )
        s.add_all([run, t0, t1])
        s.commit()

    scan_service.recover_interrupted_scans()

    # GET /api/scan/{run_id}
    res = client.get(f"/api/scan/{run_id}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["id"] == run_id
    assert detail["status"] == "interrupted"
    assert detail["error"] == "Process restarted while scan was in progress"
    assert detail["succeeded"] == 1
    assert detail["failed"] == 0
    assert detail["interrupted"] == 1
    assert detail["live"]["interrupted"] == 1
    assert detail["live"]["status"] == "interrupted"
    assert len(detail["tasks"]) == 2
    task_statuses = {t["runtime_id"]: t["status"] for t in detail["tasks"]}
    assert task_statuses[runtime_ids[0]] == "ok"
    assert task_statuses[runtime_ids[1]] == "interrupted"

    # GET /api/scans
    res_list = client.get("/api/scans")
    assert res_list.status_code == 200
    runs = res_list.json()["runs"]
    matching = next((r for r in runs if r["id"] == run_id), None)
    assert matching is not None
    assert matching["status"] == "interrupted"
    assert matching["interrupted"] == 1
    assert matching["error"] == "Process restarted while scan was in progress"

    # GET /api/overview
    res_ov = client.get("/api/overview")
    assert res_ov.status_code == 200
    recent = res_ov.json()["recent_runs"]
    ov_run = next((r for r in recent if r["id"] == run_id), None)
    assert ov_run is not None
    assert ov_run["status"] == "interrupted"
    assert ov_run["error"] == "Process restarted while scan was in progress"

    # GET /api/scan/active: should not report interrupted runs as active
    res_active = client.get("/api/scan/active")
    assert res_active.status_code == 200
    assert res_active.json()["runs"] == []


def test_lifespan_startup_recovers_interrupted_scans(fleet_db):
    """App lifespan startup executes scan_service.recover_interrupted_scans()."""
    from fastapi.testclient import TestClient
    from agent_fleet.main import app

    runtime_ids = _create_fixture_runtimes("host-lifespan-test")
    run_id = models.new_id()

    # Simulate pre-existing running scan in DB prior to process startup
    with session_scope() as s:
        run = models.ScanRun(
            id=run_id,
            status="running",
            triggered_by="manual",
            total=1,
            started_at=models.utcnow(),
        )
        task = models.ScanTask(
            id=models.new_id(),
            run_id=run_id,
            runtime_id=runtime_ids[0],
            status="running",
        )
        s.add_all([run, task])
        s.commit()

    # Starting TestClient triggers lifespan
    with TestClient(app) as test_client:
        res = test_client.get(f"/api/scan/{run_id}")
        assert res.status_code == 200
        assert res.json()["status"] == "interrupted"
        assert res.json()["tasks"][0]["status"] == "interrupted"
