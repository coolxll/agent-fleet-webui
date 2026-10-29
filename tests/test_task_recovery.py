"""Unit tests for task recovery metadata and concurrency protection."""
import pytest
from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.herdr_models import HerdrObservation
from agent_fleet.paseo_models import PaseoObservation
from agent_fleet.services import task_service as service
from agent_fleet.task_models import FleetTask, TaskStage


def _setup_task(tmp_path, status="in_progress"):
    with session_scope() as db:
        host = models.Host(name="recovery-node-1")
        db.add(host)
        db.flush()
        runtime = models.Runtime(
            host_id=host.id, key="native", transport="ssh", kind="linux", enabled=True
        )
        db.add(runtime)
        db.flush()
        task = FleetTask(
            title="Refactor Worker Subsystem",
            goal="Harden recovery pathways",
            project_path=str(tmp_path),
            status=status,
        )
        db.add(task)
        db.flush()
        return task.id, runtime.id, host.name


def test_recovery_info_missing_task(fleet_db):
    with pytest.raises(service.TaskError, match="Task not found"):
        service.get_task_recovery_info("non-existent-task-id")


def test_recovery_ssh_disconnected_scenario(fleet_db, tmp_path):
    task_id, runtime_id, host_name = _setup_task(tmp_path, status="in_progress")
    with session_scope() as db:
        stage = TaskStage(
            task_id=task_id,
            runtime_id=runtime_id,
            host_name=host_name,
            agent_id="claude",
            project_path=str(tmp_path),
            status="interrupted",
            error="Agent connection ended without a final protocol result; inspect native session before retrying",
            native_session_id="claude-sess-99",
        )
        db.add(stage)
        db.flush()

    info = service.get_task_recovery_info(task_id)
    assert info["task_id"] == task_id
    assert info["reason_category"] == "ssh_disconnected"
    assert info["target_host"] == host_name
    assert info["working_directory"] == str(tmp_path)
    assert info["execution_phase"] == "aborted"
    assert info["native_session_id"] == "claude-sess-99"
    assert info["session_locator"]["has_native_session"] is True
    assert info["safety_check"]["is_active"] is False
    assert info["safety_check"]["safe_to_retry"] is True
    assert info["safety_check"]["local_state_clear"] is True
    assert info["safety_check"]["remote_state_unknown"] is False
    assert info["can_resume"] is False
    assert info["resume_mode"] == "new_session"


def test_recovery_controller_restarted_scenario(fleet_db, tmp_path):
    task_id, runtime_id, host_name = _setup_task(tmp_path, status="in_progress")
    with session_scope() as db:
        stage = TaskStage(
            task_id=task_id,
            active_task_id=task_id,
            runtime_id=runtime_id,
            host_name=host_name,
            agent_id="codex",
            project_path=str(tmp_path),
            status="running",
        )
        db.add(stage)
        db.flush()

    # Simulate controller restart
    service.recover_interrupted()

    info = service.get_task_recovery_info(task_id)
    assert info["reason_category"] == "controller_restarted"
    assert "Fleet restarted" in info["reason_message"]
    assert info["execution_phase"] == "aborted"
    assert info["safety_check"]["is_active"] is False
    assert info["safety_check"]["local_state_clear"] is True
    assert info["safety_check"]["remote_state_verified"] is False
    assert info["safety_check"]["remote_state_unknown"] is True
    assert info["safety_check"]["safe_to_retry"] is False
    assert info["safe_to_retry"] is False
    assert info["can_resume"] is False
    assert info["resume_mode"] == "new_session"
    assert "unknown" in info["safety_check"]["message"].lower()


def test_recovery_interactive_blocked_scenario(fleet_db, tmp_path):
    task_id, runtime_id, host_name = _setup_task(tmp_path, status="waiting")
    with session_scope() as db:
        stage = TaskStage(
            task_id=task_id,
            runtime_id=runtime_id,
            host_name=host_name,
            agent_id="claude",
            project_path=str(tmp_path),
            status="blocked",
            error="Waiting for user permission to execute destructive command",
            native_session_id="claude-interactive-1",
        )
        db.add(stage)
        db.flush()

    info = service.get_task_recovery_info(task_id)
    assert info["reason_category"] == "interactive_blocked"
    assert info["execution_phase"] == "interactive"
    assert "Waiting for user permission" in info["reason_message"]
    assert info["native_session_id"] == "claude-interactive-1"
    assert info["session_locator"]["has_native_session"] is True


def test_recovery_process_failed_scenario(fleet_db, tmp_path):
    task_id, runtime_id, host_name = _setup_task(tmp_path, status="in_progress")
    with session_scope() as db:
        stage = TaskStage(
            task_id=task_id,
            runtime_id=runtime_id,
            host_name=host_name,
            agent_id="codex",
            project_path=str(tmp_path),
            status="failed",
            error="Remote agent command exited with code 127: binary not found",
            native_session_id="codex-fail-1",
        )
        db.add(stage)
        db.flush()

    info = service.get_task_recovery_info(task_id)
    assert info["reason_category"] == "process_failed"
    assert info["execution_phase"] == "failed"
    assert "binary not found" in info["reason_message"]


def test_workspace_lock_concurrency_protection(fleet_db, tmp_path):
    task_id, runtime_id, host_name = _setup_task(tmp_path, status="in_progress")

    # 1. Active task stage within the same task
    with session_scope() as db:
        stage = TaskStage(
            task_id=task_id,
            active_task_id=task_id,
            runtime_id=runtime_id,
            host_name=host_name,
            agent_id="claude",
            project_path=str(tmp_path),
            status="running",
        )
        db.add(stage)
        db.flush()

    info = service.get_task_recovery_info(task_id)
    assert info["safety_check"]["is_active"] is True
    assert info["safety_check"]["safe_to_retry"] is False
    assert info["safe_to_retry"] is False
    assert info["can_resume"] is False
    assert "active execution" in info["safety_check"]["message"]

    # 2. Complete stage for first task, but another task is actively executing in the same workspace
    with session_scope() as db:
        s1 = db.get(TaskStage, stage.id)
        s1.active_task_id = None
        s1.status = "interrupted"
        s1.error = "Agent connection dropped"

        other_task = FleetTask(
            title="Parallel Task",
            project_path=str(tmp_path),
            status="in_progress",
        )
        db.add(other_task)
        db.flush()
        conflicting_stage = TaskStage(
            task_id=other_task.id,
            active_task_id=other_task.id,
            runtime_id=runtime_id,
            host_name=host_name,
            agent_id="codex",
            project_path=str(tmp_path),
            status="running",
        )
        db.add(conflicting_stage)
        db.flush()

    # Re-evaluate recovery info for task 1
    info2 = service.get_task_recovery_info(task_id)
    assert info2["safety_check"]["is_active"] is True
    assert info2["safety_check"]["safe_to_retry"] is False
    assert info2["safe_to_retry"] is False
    assert "Workspace conflict" in info2["safety_check"]["message"]

    # 3. Clear conflicting stage: workspace becomes safe
    with session_scope() as db:
        cs = db.get(TaskStage, conflicting_stage.id)
        cs.active_task_id = None
        cs.status = "completed"

    info3 = service.get_task_recovery_info(task_id)
    assert info3["safety_check"]["is_active"] is False
    assert info3["safety_check"]["safe_to_retry"] is True
    assert info3["safe_to_retry"] is True


def test_native_session_present_vs_absent(fleet_db, tmp_path):
    task_id, runtime_id, host_name = _setup_task(tmp_path, status="failed")

    # Absent native session
    with session_scope() as db:
        stage = TaskStage(
            task_id=task_id,
            runtime_id=runtime_id,
            host_name=host_name,
            agent_id="claude",
            project_path=str(tmp_path),
            status="failed",
            error="Startup crashed before session initialization",
            native_session_id=None,
        )
        db.add(stage)
        db.flush()

    info_absent = service.get_task_recovery_info(task_id)
    assert info_absent["native_session_id"] is None
    assert info_absent["session_locator"]["has_native_session"] is False
    assert info_absent["can_resume"] is False
    assert info_absent["safe_to_retry"] is True

    # Present native session with Herdr and Paseo locators
    with session_scope() as db:
        s = db.get(TaskStage, stage.id)
        s.native_session_id = "herdr-pane-abc"

        # Mock Herdr Observation
        herdr = HerdrObservation(
            runtime_id=runtime_id,
            status="running",
            sessions=[
                {
                    "external_id": "herdr-pane-abc",
                    "workspace_id": "ws-1",
                    "provider": "claude",
                    "title": "Agent Workspace",
                }
            ],
        )
        db.add(herdr)

        # Mock Paseo Observation
        paseo = PaseoObservation(
            runtime_id=runtime_id,
            status="running",
            facts={"listen": "127.0.0.1:6767"},
            sessions=[
                {
                    "external_id": "herdr-pane-abc",
                    "name": "Paseo Service Session",
                    "cwd": str(tmp_path),
                }
            ],
        )
        db.add(paseo)

    info_present = service.get_task_recovery_info(task_id)
    assert info_present["native_session_id"] == "herdr-pane-abc"
    assert info_present["session_locator"]["has_native_session"] is True
    # Non-resumable failed stage does not falsely set can_resume=True
    assert info_present["can_resume"] is False
    assert info_present["resume_mode"] == "new_session"
    assert info_present["herdr_pane"]["pane_id"] == "herdr-pane-abc"
    assert info_present["herdr_pane"]["workspace_id"] == "ws-1"
    assert info_present["paseo_location"]["endpoint"] == "127.0.0.1:6767"
    assert info_present["paseo_location"]["session_id"] == "herdr-pane-abc"

    # Stage with completed status, healthy output and no error becomes resumable
    with session_scope() as db:
        s = db.get(TaskStage, stage.id)
        s.status = "completed"
        s.error = None
        s.output = "Successfully performed initial refactoring turn"
        db.flush()

    info_resumable = service.get_task_recovery_info(task_id)
    assert info_resumable["can_resume"] is True
    assert info_resumable["resume_mode"] == "resume_native_session"
    assert info_resumable["resume_session_id"] == "herdr-pane-abc"


@pytest.mark.asyncio
async def test_concurrent_workspace_dispatch_rejected(fleet_db, tmp_path):
    task1_id, runtime_id, host_name = _setup_task(tmp_path, status="in_progress")
    with session_scope() as db:
        task2 = FleetTask(
            title="Second Task on same workspace",
            goal="Try concurrent execution",
            project_path=str(tmp_path),
            status="todo",
        )
        db.add(task2)
        db.flush()
        task2_id = task2.id

        # Task 1 currently executing on (runtime_id, tmp_path)
        stage1 = TaskStage(
            task_id=task1_id,
            active_task_id=task1_id,
            runtime_id=runtime_id,
            host_name=host_name,
            agent_id="codex",
            project_path=str(tmp_path),
            status="running",
        )
        db.add(stage1)
        db.flush()

    draft = {
        "runtime_id": runtime_id,
        "agent_id": "codex",
        "project_path": str(tmp_path),
        "notes": "Testing conflict rejection",
        "session_mode": "new",
    }

    # 1. preview_handoff on task 2 should reject due to locked workspace
    with pytest.raises(service.TaskError) as exc_info:
        await service.preview_handoff(task2_id, draft)
    assert exc_info.value.status == 409
    assert f"Workspace is already locked by active execution in task {task1_id}" in str(exc_info.value)

    # 2. start_handoff on task 2 should also reject
    with pytest.raises(service.TaskError) as exc_info2:
        await service.start_handoff(task2_id, {**draft, "preview_hash": "dummy"})
    assert exc_info2.value.status == 409
    assert f"Workspace is already locked by active execution in task {task1_id}" in str(exc_info2.value)
