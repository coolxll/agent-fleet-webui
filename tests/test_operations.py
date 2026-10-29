"""Unit tests for Operation Audit Log (Issue #55).

Tests:
1. Operation recording and unique id generation
2. Status transitions and enforcement of verification evidence for "succeeded"
3. Sanitization of sensitive credentials, tokens, and errors
4. Multi-criteria filtering (target_type, target_id, status, operation_type)
5. Persistence and querying across DB restarts
6. API routes GET /api/operations and GET /api/operations/{op_id}
"""

import asyncio
import pytest
from datetime import datetime, timezone
from agent_fleet import models
from agent_fleet.db import session_scope, init_engine, create_all
from agent_fleet.services import operation_service


def test_operation_record_and_get(fleet_db):
    """Test creating an operation log and fetching it by ID."""
    op = operation_service.record_operation(
        operation_type="agent_upgrade",
        target_type="runtime",
        target_id="rt-test-123",
        target_name="corp172:claude",
        status="accepted",
        requested_by="admin",
        details={"version": "1.0.0", "target_version": "1.1.0"},
    )

    assert op["id"].startswith("op-")
    assert op["operation_type"] == "agent_upgrade"
    assert op["target_type"] == "runtime"
    assert op["target_id"] == "rt-test-123"
    assert op["target_name"] == "corp172:claude"
    assert op["status"] == "accepted"
    assert op["requested_by"] == "admin"
    assert op["started_at"] is not None
    assert op["finished_at"] is None
    assert op["error"] is None
    assert op["details"]["version"] == "1.0.0"

    fetched = operation_service.get_operation(op["id"])
    assert fetched is not None
    assert fetched["id"] == op["id"]
    assert fetched["status"] == "accepted"


def test_status_transition_verification_enforcement(fleet_db):
    """Enforce that transitioning to 'succeeded' MUST provide remote verification evidence."""
    op = operation_service.record_operation(
        operation_type="runner_start",
        target_type="runner",
        target_id="runner-01",
        status="running",
    )

    # Transitioning to 'succeeded' without verification evidence must fail
    with pytest.raises(ValueError, match="without remote verification evidence"):
        operation_service.update_operation_status(op["id"], status="succeeded")

    # Recording directly as 'succeeded' without verification evidence must also fail
    with pytest.raises(ValueError, match="without remote verification evidence"):
        operation_service.record_operation(
            operation_type="runner_start",
            target_type="runner",
            target_id="runner-02",
            status="succeeded",
        )

    # Transitioning with verification evidence succeeds
    evidence = {"exit_code": 0, "status": "active", "scan_refreshed": True}
    updated = operation_service.update_operation_status(
        op["id"],
        status="succeeded",
        verification_evidence=evidence,
    )
    assert updated["status"] == "succeeded"
    assert updated["finished_at"] is not None
    assert updated["details"]["verification"]["scan_refreshed"] is True

    # Transitioning to failed does not require verification evidence
    op_fail = operation_service.record_operation(
        operation_type="runner_stop",
        target_type="runner",
        target_id="runner-03",
        status="running",
    )
    failed_op = operation_service.update_operation_status(
        op_fail["id"],
        status="failed",
        error="Process timeout",
    )
    assert failed_op["status"] == "failed"
    assert failed_op["error"] == "Process timeout"
    assert failed_op["finished_at"] is not None


def test_invalid_status_rejected(fleet_db):
    """Attempting an invalid status raises ValueError."""
    with pytest.raises(ValueError, match="Invalid operation status"):
        operation_service.record_operation(
            operation_type="task_dispatch",
            target_type="task",
            target_id="t-1",
            status="non_existent_status",
        )


def test_error_and_details_sanitization(fleet_db):
    """Ensure credentials, tokens, private keys and secrets are masked."""
    raw_error = (
        "Failed authentication with token: ghp_1234567890abcdef1234567890abcdef "
        "and sk-ant-api03-abcdef1234567890123456 and password=super_secret_value"
    )
    raw_details = {
        "api_key": "secret_api_key_12345",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0\n-----END RSA PRIVATE KEY-----",
        "password": "mypassword123",
        "nested": {
            "token": "ghp_abcdefabcdefabcdefabcdef123456",
            "safe_field": "public_data",
        },
    }

    op = operation_service.record_operation(
        operation_type="ssh_test",
        target_type="host",
        target_id="host-sensitive",
        status="failed",
        error=raw_error,
        details=raw_details,
    )

    # Check error sanitization
    assert "ghp_1234567890abcdef1234567890abcdef" not in op["error"]
    assert "***MASKED" in op["error"]
    assert "super_secret_value" not in op["error"]

    # Check details sanitization
    assert op["details"]["api_key"] == "***MASKED***"
    assert op["details"]["password"] == "***MASKED***"
    assert op["details"]["private_key"] == "***MASKED***"
    assert op["details"]["nested"]["token"] == "***MASKED***"
    assert op["details"]["nested"]["safe_field"] == "public_data"


def test_filtering_and_pagination(fleet_db):
    """Verify listing operations with multiple filter combinations."""
    # Seed 5 operations
    operation_service.record_operation(
        operation_type="agent_upgrade", target_type="runtime", target_id="rt-A", status="accepted"
    )
    operation_service.record_operation(
        operation_type="agent_upgrade", target_type="runtime", target_id="rt-B", status="running"
    )
    operation_service.record_operation(
        operation_type="runner_start",
        target_type="runner",
        target_id="run-1",
        status="succeeded",
        verification_evidence={"verified": True},
    )
    operation_service.record_operation(
        operation_type="runner_stop", target_type="runner", target_id="run-1", status="failed", error="err"
    )
    operation_service.record_operation(
        operation_type="task_dispatch", target_type="task", target_id="task-99", status="accepted"
    )

    # Target type filter
    runtime_ops = operation_service.list_operations(target_type="runtime")
    assert len(runtime_ops) == 2
    assert all(o["target_type"] == "runtime" for o in runtime_ops)

    # Target id filter
    run1_ops = operation_service.list_operations(target_id="run-1")
    assert len(run1_ops) == 2
    assert all(o["target_id"] == "run-1" for o in run1_ops)

    # Status filter
    succeeded_ops = operation_service.list_operations(status="succeeded")
    assert len(succeeded_ops) == 1
    assert succeeded_ops[0]["target_id"] == "run-1"

    # Operation type filter
    task_ops = operation_service.list_operations(operation_type="task_dispatch")
    assert len(task_ops) == 1
    assert task_ops[0]["target_type"] == "task"

    # Limit test
    limited_ops = operation_service.list_operations(limit=2)
    assert len(limited_ops) == 2


def test_querying_after_db_restart(fleet_db):
    """Verify that operations persist across DB engine re-initialization."""
    op = operation_service.record_operation(
        operation_type="agent_upgrade",
        target_type="runtime",
        target_id="rt-restart-test",
        status="succeeded",
        verification_evidence={"version_check": "1.2.3"},
    )
    op_id = op["id"]

    # Re-initialize engine pointing to the same SQLite file
    init_engine(fleet_db.db_path)
    create_all()

    # Query again
    re_queried = operation_service.get_operation(op_id)
    assert re_queried is not None
    assert re_queried["id"] == op_id
    assert re_queried["status"] == "succeeded"
    assert re_queried["details"]["verification"]["version_check"] == "1.2.3"


def test_api_routes(client, fleet_db):
    """Verify GET /api/operations and GET /api/operations/{op_id} endpoints."""
    # Seed operation
    op = operation_service.record_operation(
        operation_type="agent_upgrade",
        target_type="runtime",
        target_id="rt-api-1",
        target_name="host1:codex",
        status="succeeded",
        verification_evidence={"verified": True},
    )

    # Test list endpoint
    resp = client.get("/api/operations")
    assert resp.status_code == 200
    data = resp.json()
    assert "operations" in data
    assert data["total"] >= 1
    assert any(o["id"] == op["id"] for o in data["operations"])

    # Test list with filters
    resp_filtered = client.get("/api/operations?target_type=runtime&status=succeeded")
    assert resp_filtered.status_code == 200
    filtered_data = resp_filtered.json()
    assert any(o["id"] == op["id"] for o in filtered_data["operations"])

    resp_empty = client.get("/api/operations?target_type=nonexistent")
    assert resp_empty.status_code == 200
    assert resp_empty.json()["total"] == 0

    # Test get by ID endpoint
    resp_get = client.get(f"/api/operations/{op['id']}")
    assert resp_get.status_code == 200
    item = resp_get.json()["operation"]
    assert item["id"] == op["id"]
    assert item["target_name"] == "host1:codex"

    # Test 404 for unknown operation
    resp_404 = client.get("/api/operations/op-nonexistent")
    assert resp_404.status_code == 404


def test_fsm_terminal_state_non_regression(fleet_db):
    """Enforce that terminal states (succeeded, failed) cannot regress to running/accepted."""
    # Succeeded cannot regress
    op = operation_service.record_operation(
        operation_type="agent_upgrade",
        target_type="runtime",
        target_id="rt-fsm-1",
        status="running",
    )
    succeeded = operation_service.update_operation_status(
        op["id"],
        status="succeeded",
        verification_evidence={"version": "1.2.0"},
    )
    assert succeeded["status"] == "succeeded"

    # Attempting to regress succeeded -> running with strict=False is safely ignored
    regressed = operation_service.update_operation_status(op["id"], status="running")
    assert regressed["status"] == "succeeded"

    # Details can still be updated idempotently while in terminal status
    updated_details = operation_service.update_operation_status(
        op["id"], status="succeeded", details={"extra_note": "verified"}
    )
    assert updated_details["status"] == "succeeded"
    assert updated_details["details"]["extra_note"] == "verified"

    # Attempting to regress succeeded -> running with strict=True raises ValueError
    with pytest.raises(ValueError, match="Invalid operation status transition"):
        operation_service.update_operation_status(op["id"], status="running", strict=True)

    # Attempting to regress succeeded -> accepted with strict=True raises ValueError
    with pytest.raises(ValueError, match="Invalid operation status transition"):
        operation_service.update_operation_status(op["id"], status="accepted", strict=True)

    # Failed cannot regress
    op_fail = operation_service.record_operation(
        operation_type="agent_upgrade",
        target_type="runtime",
        target_id="rt-fsm-2",
        status="running",
    )
    failed = operation_service.update_operation_status(
        op_fail["id"],
        status="failed",
        error="Upgrade script timed out",
    )
    assert failed["status"] == "failed"

    # Regress failed -> running with strict=False ignored
    regressed_fail = operation_service.update_operation_status(op_fail["id"], status="running")
    assert regressed_fail["status"] == "failed"

    # Regress failed -> running with strict=True raises ValueError
    with pytest.raises(ValueError, match="Invalid operation status transition"):
        operation_service.update_operation_status(op_fail["id"], status="running", strict=True)

    # Unknown can transition to succeeded or failed with verification
    op_unknown = operation_service.record_operation(
        operation_type="agent_upgrade",
        target_type="runtime",
        target_id="rt-fsm-3",
        status="running",
    )
    operation_service.update_operation_status(op_unknown["id"], status="unknown")
    assert operation_service.get_operation(op_unknown["id"])["status"] == "unknown"

    # unknown cannot regress to running
    with pytest.raises(ValueError, match="Invalid operation status transition"):
        operation_service.update_operation_status(op_unknown["id"], status="running", strict=True)

    # unknown -> succeeded with evidence is valid
    recovered = operation_service.update_operation_status(
        op_unknown["id"],
        status="succeeded",
        verification_evidence={"verified_after_reconcile": True},
    )
    assert recovered["status"] == "succeeded"


def test_reconcile_orphaned_operations(fleet_db):
    """Test reconciling orphaned running and accepted operations on controller restart."""
    op1 = operation_service.record_operation(
        operation_type="agent_upgrade", target_type="runtime", target_id="rt-rec-1", status="accepted"
    )
    op2 = operation_service.record_operation(
        operation_type="agent_upgrade", target_type="runtime", target_id="rt-rec-2", status="running"
    )
    op3 = operation_service.record_operation(
        operation_type="runner_start",
        target_type="runner",
        target_id="run-rec-3",
        status="succeeded",
        verification_evidence={"verified": True},
    )
    op4 = operation_service.record_operation(
        operation_type="runner_stop", target_type="runner", target_id="run-rec-4", status="failed", error="err"
    )

    reconciled_count = operation_service.reconcile_orphaned_operations()
    assert reconciled_count == 2

    # op1 and op2 transitioned to 'unknown' with restart notice
    rec_op1 = operation_service.get_operation(op1["id"])
    rec_op2 = operation_service.get_operation(op2["id"])
    assert rec_op1["status"] == "unknown"
    assert "Controller restarted" in rec_op1["error"]
    assert rec_op1["finished_at"] is not None

    assert rec_op2["status"] == "unknown"
    assert "Controller restarted" in rec_op2["error"]
    assert rec_op2["finished_at"] is not None

    # op3 and op4 remain untouched
    rec_op3 = operation_service.get_operation(op3["id"])
    rec_op4 = operation_service.get_operation(op4["id"])
    assert rec_op3["status"] == "succeeded"
    assert rec_op4["status"] == "failed"

    # Second run is idempotent
    assert operation_service.reconcile_orphaned_operations() == 0


@pytest.mark.asyncio
async def test_background_upgrade_writeback_succeeded(fleet_db, monkeypatch):
    """Background upgrade task writes back succeeded status and verification evidence to OperationLog."""
    from unittest.mock import AsyncMock
    from agent_fleet.remotes.executor import ExecResult
    from agent_fleet.services import action_service

    # Seed runtime and installation
    with session_scope() as s:
        host = models.Host(id="h-up-1", name="host-up-1", enabled=True)
        rt = models.Runtime(id="rt-up-1", key="rt-up-1", host_id="h-up-1", transport="local", enabled=True)
        inst = models.AgentInstallation(
            id="inst-up-1",
            runtime_id="rt-up-1",
            agent_id="codex",
            version="0.1.0",
            detected=True,
        )
        s.add(host)
        s.add(rt)
        s.add(inst)

    op = operation_service.record_operation(
        operation_type="agent_upgrade",
        target_type="runtime",
        target_id="rt-up-1",
        target_name="local:codex",
        status="running",
        details={"installation_id": "inst-up-1"},
    )

    mock_runner = AsyncMock()
    mock_runner.run_script.return_value = ExecResult(stdout="installed @openai/codex@latest", exit_code=0)
    monkeypatch.setattr(action_service, "build_runner", lambda *args, **kwargs: mock_runner)

    mock_scan_res = AsyncMock()
    mock_scan_res.ok = True
    monkeypatch.setattr(action_service, "scan_runtime", AsyncMock(return_value=mock_scan_res))
    monkeypatch.setattr(action_service, "persist_result", lambda *args, **kwargs: None)

    task = action_service.start_upgrade_task("inst-up-1", operation_id=op["id"])
    assert task["operation_id"] == op["id"]

    # Wait for the background task to complete
    for _ in range(50):
        t = action_service.get_upgrade_task(task["task_id"])
        if t and t.get("status") in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)

    final_op = operation_service.get_operation(op["id"])
    assert final_op["status"] == "succeeded"
    assert final_op["details"]["verification"]["exit_code"] == 0
    assert final_op["details"]["verification"]["scan_refreshed"] is True


@pytest.mark.asyncio
async def test_background_upgrade_writeback_failed(fleet_db, monkeypatch):
    """Background upgrade task writes back failed status to OperationLog on error."""
    from unittest.mock import AsyncMock
    from agent_fleet.remotes.executor import ExecResult
    from agent_fleet.services import action_service

    with session_scope() as s:
        host = models.Host(id="h-up-2", name="host-up-2", enabled=True)
        rt = models.Runtime(id="rt-up-2", key="rt-up-2", host_id="h-up-2", transport="local", enabled=True)
        inst = models.AgentInstallation(
            id="inst-up-2",
            runtime_id="rt-up-2",
            agent_id="claude",
            version="1.0.0",
            detected=True,
        )
        s.add(host)
        s.add(rt)
        s.add(inst)

    op = operation_service.record_operation(
        operation_type="agent_upgrade",
        target_type="runtime",
        target_id="rt-up-2",
        target_name="local:claude",
        status="running",
        details={"installation_id": "inst-up-2"},
    )

    mock_runner = AsyncMock()
    mock_runner.run_script.return_value = ExecResult(
        stdout="", stderr="npm ERR! 404 Not Found", exit_code=1
    )
    monkeypatch.setattr(action_service, "build_runner", lambda *args, **kwargs: mock_runner)

    task = action_service.start_upgrade_task("inst-up-2", operation_id=op["id"])

    for _ in range(50):
        t = action_service.get_upgrade_task(task["task_id"])
        if t and t.get("status") in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)

    final_op = operation_service.get_operation(op["id"])
    assert final_op["status"] == "failed"
    assert "npm ERR! 404" in final_op["error"]


def test_batch_upgrade_tracks_operation_ids(fleet_db, monkeypatch):
    """Batch upgrade accepts operation_ids and links each task."""
    from unittest.mock import MagicMock
    from agent_fleet.services import action_service

    with session_scope() as s:
        host = models.Host(id="h-up-3", name="host-up-3", enabled=True)
        rt = models.Runtime(id="rt-up-3", key="rt-up-3", host_id="h-up-3", transport="local", enabled=True)
        inst1 = models.AgentInstallation(
            id="inst-batch-1",
            runtime_id="rt-up-3",
            agent_id="codex",
            version="1.0.0",
            detected=True,
        )
        inst2 = models.AgentInstallation(
            id="inst-batch-2",
            runtime_id="rt-up-3",
            agent_id="claude",
            version="1.0.0",
            detected=True,
        )
        s.add(host)
        s.add(rt)
        s.add(inst1)
        s.add(inst2)

    monkeypatch.setattr(action_service, "build_runner", lambda *args, **kwargs: MagicMock())

    tasks = action_service.start_batch_upgrade(
        ["inst-batch-1", "inst-batch-2"],
        operation_ids={"inst-batch-1": "op-batch-1", "inst-batch-2": "op-batch-2"},
    )
    assert len(tasks) == 2
    assert tasks[0]["operation_id"] == "op-batch-1"
    assert tasks[1]["operation_id"] == "op-batch-2"
