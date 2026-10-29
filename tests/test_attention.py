"""Unit tests for Attention aggregation service and API."""
from datetime import timedelta
import pytest
from sqlalchemy import delete

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.task_models import FleetTask, TaskStage
from agent_fleet.services import attention_service


def test_empty_state_all_healthy(fleet_db):
    """When all hosts are scanned recently and healthy, and no tasks blocked, items are empty."""
    with session_scope() as db:
        db.execute(delete(TaskStage))
        db.execute(delete(FleetTask))
        db.execute(delete(models.Runtime))
        db.execute(delete(models.Host))

        now = models.utcnow()
        host = models.Host(name="healthy-host", enabled=True)
        db.add(host)
        db.flush()

        rt = models.Runtime(
            host_id=host.id,
            key="native",
            reachable=True,
            last_scan_at=now,
            last_success_at=now,
            last_error=None,
            enabled=True,
        )
        db.add(rt)
        db.flush()

        res = attention_service.get_attention_summary(db=db)
        assert res["total"] == 0
        assert res["items"] == []
        assert res["counts"]["error"] == 0
        assert res["counts"]["warning"] == 0


def test_never_scanned_host(fleet_db):
    """Enabled host with no scan timestamps is flagged as scan_stale (subtype: never_scanned)."""
    with session_scope() as db:
        db.execute(delete(TaskStage))
        db.execute(delete(FleetTask))
        db.execute(delete(models.Runtime))
        db.execute(delete(models.Host))

        host = models.Host(name="new-host", enabled=True)
        db.add(host)
        db.flush()
        rt = models.Runtime(host_id=host.id, key="native", enabled=True)
        db.add(rt)
        db.flush()

        res = attention_service.get_attention_summary(db=db)
        assert res["total"] == 1
        assert res["counts"]["warning"] == 1
        assert res["counts"]["error"] == 0
        item = res["items"][0]
        assert item["id"] == f"host:{host.id}:scan_stale"
        assert item["type"] == "scan_stale"
        assert item["severity"] == "warning"
        assert item["target_type"] == "host"
        assert item["target_id"] == host.id
        assert item["action_payload"]["subtype"] == "never_scanned"


def test_stale_scan_host(fleet_db):
    """Host probed longer than 24h ago is flagged as scan_stale (subtype: stale)."""
    with session_scope() as db:
        db.execute(delete(TaskStage))
        db.execute(delete(FleetTask))
        db.execute(delete(models.Runtime))
        db.execute(delete(models.Host))

        old_time = models.utcnow() - timedelta(days=2)
        host = models.Host(name="stale-host", enabled=True)
        db.add(host)
        db.flush()
        rt = models.Runtime(
            host_id=host.id,
            key="native",
            reachable=True,
            last_scan_at=old_time,
            last_success_at=old_time,
            last_error=None,
            enabled=True,
        )
        db.add(rt)
        db.flush()

        res = attention_service.get_attention_summary(db=db, stale_threshold_seconds=86400)
        assert res["total"] == 1
        assert res["counts"]["warning"] == 1
        item = res["items"][0]
        assert item["id"] == f"host:{host.id}:scan_stale"
        assert item["type"] == "scan_stale"
        assert item["severity"] == "warning"
        assert item["action_payload"]["subtype"] == "stale"
        assert item["action_payload"]["age_seconds"] >= 86400 * 2 - 10


def test_scan_failed_host(fleet_db):
    """Host with error in runtime probe is flagged as scan_failed with error severity."""
    with session_scope() as db:
        db.execute(delete(TaskStage))
        db.execute(delete(FleetTask))
        db.execute(delete(models.Runtime))
        db.execute(delete(models.Host))

        now = models.utcnow()
        host = models.Host(name="failed-host", enabled=True)
        db.add(host)
        db.flush()
        rt = models.Runtime(
            host_id=host.id,
            key="native",
            reachable=False,
            last_scan_at=now,
            last_error="Connection refused: 22",
            enabled=True,
        )
        db.add(rt)
        db.flush()

        res = attention_service.get_attention_summary(db=db)
        assert res["total"] == 1
        assert res["counts"]["error"] == 1
        assert res["counts"]["warning"] == 0
        item = res["items"][0]
        assert item["id"] == f"host:{host.id}:scan_failed"
        assert item["type"] == "scan_failed"
        assert item["severity"] == "error"
        assert item["target_type"] == "host"
        assert "Connection refused" in item["message"]


def test_task_blocked_interrupted_failed(fleet_db):
    """Tasks with blocked, interrupted, or failed execution state are flagged."""
    with session_scope() as db:
        db.execute(delete(TaskStage))
        db.execute(delete(FleetTask))
        db.execute(delete(models.Runtime))
        db.execute(delete(models.Host))

        t1 = FleetTask(title="Blocked Task", status="waiting")
        db.add(t1)
        db.flush()
        s1 = TaskStage(task_id=t1.id, agent_id="claude", status="blocked", error="User auth token expired")
        db.add(s1)

        t2 = FleetTask(title="Failed Task", status="in_progress")
        db.add(t2)
        db.flush()
        s2 = TaskStage(task_id=t2.id, agent_id="codex", status="failed", error="Execution timeout")
        db.add(s2)

        t3 = FleetTask(title="Interrupted Task", status="todo")
        db.add(t3)
        db.flush()
        s3 = TaskStage(task_id=t3.id, agent_id="codex", status="interrupted")
        db.add(s3)

        db.flush()

        res = attention_service.get_attention_summary(db=db)
        assert res["total"] == 3
        # Failed is error, blocked and interrupted are warnings
        assert res["counts"]["error"] == 1
        assert res["counts"]["warning"] == 2

        task_items = {it["target_id"]: it for it in res["items"]}
        assert task_items[t1.id]["severity"] == "warning"
        assert task_items[t1.id]["type"] == "task_blocked"
        assert task_items[t1.id]["action_view"] == "tasks"
        assert task_items[t2.id]["severity"] == "error"
        assert "timeout" in task_items[t2.id]["message"]
        assert task_items[t3.id]["severity"] == "warning"


def test_status_recovery_and_deduplication(fleet_db):
    """Test stable deduplication ID and recovery once scan succeeds or task finishes."""
    with session_scope() as db:
        db.execute(delete(TaskStage))
        db.execute(delete(FleetTask))
        db.execute(delete(models.Runtime))
        db.execute(delete(models.Host))

        now = models.utcnow()
        host = models.Host(name="recovering-host", enabled=True)
        db.add(host)
        db.flush()
        rt = models.Runtime(
            host_id=host.id,
            key="native",
            reachable=False,
            last_scan_at=now,
            last_error="Temporary SSH drop",
            enabled=True,
        )
        db.add(rt)

        task = FleetTask(title="Task to complete", status="waiting")
        db.add(task)
        db.flush()
        stage = TaskStage(task_id=task.id, agent_id="claude", status="blocked")
        db.add(stage)
        db.flush()

        # Check before recovery
        res1 = attention_service.get_attention_summary(db=db)
        assert res1["total"] == 2
        host_item = next(it for it in res1["items"] if it["target_type"] == "host")
        task_item = next(it for it in res1["items"] if it["target_type"] == "task")
        assert host_item["id"] == f"host:{host.id}:scan_failed"
        assert task_item["id"] == f"task:{task.id}:task_blocked"

        # Recover host scan
        rt.last_error = None
        rt.reachable = True
        rt.last_success_at = models.utcnow()

        # Recover task: complete stage and set task to done
        stage.status = "completed"
        task.status = "done"
        db.flush()

        # Check after recovery
        res2 = attention_service.get_attention_summary(db=db)
        assert res2["total"] == 0
        assert res2["items"] == []


def test_api_route(client):
    """Test GET /api/attention endpoint and pagination/limit."""
    with session_scope() as db:
        db.execute(delete(TaskStage))
        db.execute(delete(FleetTask))
        db.execute(delete(models.Runtime))
        db.execute(delete(models.Host))

        for i in range(5):
            h = models.Host(name=f"host-{i}", enabled=True)
            db.add(h)
            db.flush()
            rt = models.Runtime(
                host_id=h.id,
                key="native",
                reachable=False,
                last_scan_at=models.utcnow(),
                last_error=f"Error {i}",
                enabled=True,
            )
            db.add(rt)
        db.flush()

    resp = client.get("/api/attention?limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 3
    assert data["counts"]["error"] == 5
