"""Integration of work history and Paseo with existing inventory operations."""

import pytest

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.paseo_models import PaseoObservation
from agent_fleet.task_models import FleetTask, TaskStage


@pytest.mark.parametrize("remove_host", [False, True])
def test_inventory_removal_preserves_task_history_and_removes_paseo(client, remove_host):
    with session_scope() as db:
        host = models.Host(name="work-integration-host")
        db.add(host)
        db.flush()
        runtime = models.Runtime(host_id=host.id, key="native", transport="ssh")
        db.add(runtime)
        db.flush()
        session = models.Session(runtime_id=runtime.id, agent_id="claude", session_key="native-session")
        task = FleetTask(title="Keep this task after inventory cleanup", status="review")
        db.add_all([session, task])
        db.flush()
        stage = TaskStage(
            task_id=task.id,
            runtime_id=runtime.id,
            session_id=session.id,
            source_session_id=session.id,
            host_name=host.name,
            agent_id="claude",
            native_session_id=session.session_key,
            project_path="/workspace/project",
            status="completed",
            output="Changes ready for review",
        )
        db.add_all([stage, PaseoObservation(runtime_id=runtime.id, status="running")])
        db.flush()
        host_id, runtime_id, task_id, stage_id = host.id, runtime.id, task.id, stage.id

    endpoint = f"/api/hosts/{host_id}" if remove_host else f"/api/runtimes/{runtime_id}"
    response = client.delete(endpoint)
    assert response.status_code == 200
    with session_scope() as db:
        assert db.get(PaseoObservation, runtime_id) is None
        assert db.get(FleetTask, task_id).status == "review"
        stage = db.get(TaskStage, stage_id)
        assert stage.runtime_id is None
        assert stage.session_id is None
        assert stage.source_session_id is None
        assert stage.host_name == "work-integration-host"
        assert stage.project_path == "/workspace/project"
        assert stage.output == "Changes ready for review"


def test_work_features_are_registered_in_the_application(client):
    assert client.get("/api/tasks").status_code == 200
    response = client.get("/api/paseo")
    assert response.status_code == 200
    assert response.json() == {"items": []}
