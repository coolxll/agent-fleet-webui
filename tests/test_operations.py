"""Unit tests for Operation Audit Log (Issue #55).

Tests:
1. Operation recording and unique id generation
2. Status transitions and enforcement of verification evidence for "succeeded"
3. Sanitization of sensitive credentials, tokens, and errors
4. Multi-criteria filtering (target_type, target_id, status, operation_type)
5. Persistence and querying across DB restarts
6. API routes GET /api/operations and GET /api/operations/{op_id}
"""

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
