"""Task management and native protocol integration; never starts a real Agent."""
import asyncio
import json

import pytest
from sqlalchemy import select, delete

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.task_models import FleetTask, TaskStage
from agent_fleet.services import task_service as service
from agent_fleet.services.scanner import RuntimeRef


def seed(tmp_path):
    with session_scope() as db:
        host = models.Host(name='test-host')
        db.add(host); db.flush()
        runtime = models.Runtime(host_id=host.id, key='native', transport='local', kind='linux', enabled=True)
        db.add(runtime); db.flush()
        source = models.Session(runtime_id=runtime.id, agent_id='claude', session_key='source-native',
            title='Source', project_path=str(tmp_path), updated_at=models.utcnow())
        db.add(source); db.flush()
        db.add(models.SessionTranscript(session_id=source.id, turns=[
            {'role': 'user', 'content': 'Preserve current work'},
            {'role': 'assistant', 'content': 'Visible progress', 'thinking': 'PRIVATE REASONING'}]))
        task = FleetTask(title='Task', goal='Complete feature', project_path=str(tmp_path))
        db.add(task); db.flush()
        return task.id, runtime.id, source.id


def target(runtime_id, tmp_path, **extra):
    return {'runtime_id': runtime_id, 'agent_id': 'codex', 'project_path': str(tmp_path), 'notes': 'Check tests', 'session_mode': 'auto', **extra}


def test_board_api(client, tmp_path):
    response = client.post('/api/tasks', json={'title': '  Task  ', 'goal': 'Goal'})
    assert response.status_code == 201
    task_id = response.json()['id']
    assert response.json()['title'] == 'Task'
    assert client.patch('/api/tasks/' + task_id, json={'status': 'review'}).json()['status'] == 'review'
    assert client.get('/api/tasks').json()['tasks'][0]['id'] == task_id
    assert client.patch('/api/tasks/' + task_id, json={'status': 'bad'}).status_code == 422
    assert client.patch('/api/tasks/' + task_id, json={'goal': None}).status_code == 422
    assert client.post('/api/tasks', json={'title': '  '}).status_code == 422
    assert client.get('/api/tasks/missing').status_code == 404


def test_link_and_retained_history(client, tmp_path):
    task_id, runtime_id, source_id = seed(tmp_path)
    response = client.post(f'/api/tasks/{task_id}/sessions', json={'session_id': source_id})
    assert response.status_code == 200
    assert response.json()['stages'][0]['status'] == 'unknown'
    with session_scope() as db:
        db.execute(delete(models.Runtime).where(models.Runtime.id == runtime_id))
    task = service.get_task(task_id)
    assert task['stages'][0]['runtime_id'] is None
    assert task['stages'][0]['session_id'] is None
    assert task['stages'][0]['host_name'] == 'test-host'


@pytest.mark.asyncio
async def test_preview_visible_context_resume_and_new(fleet_db, tmp_path):
    task_id, runtime_id, source_id = seed(tmp_path)
    service.link_session(task_id, source_id)
    preview = await service.preview_handoff(task_id, target(runtime_id, tmp_path))
    assert 'Visible progress' in preview['prompt']
    assert 'PRIVATE REASONING' not in preview['prompt']
    assert preview['resume_session_id'] is None
    resume = await service.preview_handoff(task_id, target(runtime_id, tmp_path, agent_id='claude'))
    assert resume['resume_session_id'] == 'source-native'
    assert 'Visible progress' not in resume['prompt']  # already in the original native context
    new = await service.preview_handoff(task_id, target(runtime_id, tmp_path, agent_id='claude', session_mode='new'))
    assert new['resume_session_id'] is None
    assert 'Visible progress' in new['prompt']
    with pytest.raises(service.TaskError, match='changed'):
        await service.start_handoff(task_id, target(runtime_id, tmp_path, preview_hash='0' * 64))


@pytest.mark.asyncio
async def test_preview_target_validation_and_shell_safety(fleet_db, tmp_path):
    task_id, runtime_id, _ = seed(tmp_path)
    unusual = tmp_path / "directory ' $(touch SHOULD_NOT_EXIST) `touch BAD`"
    unusual.mkdir()
    preview = await service.preview_handoff(task_id, target(runtime_id, unusual))
    assert preview['target_git']['exists'] is True
    assert not (tmp_path / 'SHOULD_NOT_EXIST').exists()
    assert not (tmp_path / 'BAD').exists()
    with pytest.raises(service.TaskError, match='does not exist'):
        await service.preview_handoff(task_id, target(runtime_id, tmp_path / 'missing'))
    with session_scope() as db:
        db.get(models.Runtime, runtime_id).transport = 'windows'
    with pytest.raises(service.TaskError, match='POSIX'):
        await service.preview_handoff(task_id, target(runtime_id, tmp_path))


def fake_agent(tmp_path, *, claude=False, failure=False, hang=False, resume=False):
    executable = tmp_path / 'fake-agent'
    stopped = tmp_path / 'stopped'
    script = '''#!/usr/bin/env python3
import json,sys,signal,time
from pathlib import Path
STOPPED = %r
CLAUDE = %r
FAILURE = %r
HANG = %r
RESUME = %r
def emit(value): print(json.dumps(value), flush=True)
def stop(*_):
 Path(STOPPED).write_text('stopped')
 sys.exit(0)
signal.signal(signal.SIGTERM, stop)
if CLAUDE:
 if RESUME: assert '--resume' in sys.argv
 prompt=sys.stdin.read()
 assert 'Task' in prompt
 emit({'type':'system','subtype':'init','session_id':'claude-native'})
 if HANG:
  while True: time.sleep(0.05)
 emit({'type':'assistant','message':{'content':[{'type':'thinking','thinking':'SECRET'},{'type':'text','text':'Claude finished'}]}})
 emit({'type':'result','subtype':'success','is_error':False,'permission_denials':[]})
else:
 first=json.loads(sys.stdin.readline()); assert first['method']=='initialize'
 emit({'id':1,'result':{}})
 assert json.loads(sys.stdin.readline())['method']=='initialized'
 thread=json.loads(sys.stdin.readline())
 assert thread['method']==('thread/resume' if RESUME else 'thread/start')
 assert thread['params']['sandbox']=='workspaceWrite'
 if FAILURE:
  emit({'id':2,'error':{'message':'Missing native session'}})
 else:
  emit({'id':2,'result':{'thread':{'id':'codex-native'}}})
  turn=json.loads(sys.stdin.readline()); assert turn['method']=='turn/start'
  assert 'Task' in turn['params']['input'][0]['text']
  emit({'id':3,'result':{'turn':{'id':'turn1','status':'inProgress'}}})
  if HANG:
   while True: time.sleep(0.05)
  emit({'method':'item/agentMessage/delta','params':{'delta':'Codex finished'}})
  emit({'method':'turn/completed','params':{'turn':{'status':'completed'}}})
  while sys.stdin.read(1): pass
''' % (str(stopped), claude, failure, hang, resume)
    executable.write_text(script)
    executable.chmod(0o755)
    return str(executable), stopped


def execution_stage(task_id, runtime_id, tmp_path, agent='codex'):
    with session_scope() as db:
        stage = TaskStage(task_id=task_id, active_task_id=task_id, runtime_id=runtime_id,
            agent_id=agent, host_name='test-host', project_path=str(tmp_path))
        db.add(stage); db.flush()
        return stage.id


@pytest.mark.asyncio
@pytest.mark.parametrize('claude,resume', [(False, False), (False, True), (True, False), (True, True)])
async def test_real_bridge_fake_native_protocol(fleet_db, tmp_path, claude, resume):
    task_id, runtime_id, _ = seed(tmp_path)
    agent = 'claude' if claude else 'codex'
    stage_id = execution_stage(task_id, runtime_id, tmp_path, agent)
    executable, _ = fake_agent(tmp_path, claude=claude, resume=resume)
    await asyncio.wait_for(service._execute(stage_id, RuntimeRef(id=runtime_id, transport='local'), executable,
        agent, str(tmp_path), 'Task goal', 'existing-native' if resume else None), timeout=25)
    task = service.get_task(task_id)
    assert task['status'] == 'review'
    stage = task['stages'][0]
    assert stage['status'] == 'completed'
    assert stage['native_session_id'] == agent + '-native'
    assert 'finished' in stage['output']
    assert 'SECRET' not in stage['output']
    with session_scope() as db:
        assert db.get(TaskStage, stage_id).active_task_id is None


@pytest.mark.asyncio
async def test_resume_rejection_is_failure_without_fresh_partial_context(fleet_db, tmp_path):
    task_id, runtime_id, _ = seed(tmp_path)
    stage_id = execution_stage(task_id, runtime_id, tmp_path)
    executable, _ = fake_agent(tmp_path, failure=True, resume=True)
    await asyncio.wait_for(service._execute(stage_id, RuntimeRef(id=runtime_id, transport='local'), executable,
        'codex', str(tmp_path), 'Task', 'missing-session'), timeout=25)
    stage = service.get_task(task_id)['stages'][0]
    assert stage['status'] == 'failed'
    assert 'Missing native session' in stage['error']


@pytest.mark.asyncio
async def test_cancel_releases_lock_and_terminates_agent(fleet_db, tmp_path):
    task_id, runtime_id, _ = seed(tmp_path)
    stage_id = execution_stage(task_id, runtime_id, tmp_path)
    executable, stopped = fake_agent(tmp_path, hang=True)
    job = asyncio.create_task(service._execute(stage_id, RuntimeRef(id=runtime_id, transport='local'), executable,
        'codex', str(tmp_path), 'Task'))
    service._jobs[stage_id] = job
    try:
        for _ in range(100):
            if service.get_task(task_id)['stages'][0]['status'] == 'running': break
            await asyncio.sleep(0.02)
        assert service.get_task(task_id)['stages'][0]['status'] == 'running'
        with pytest.raises(service.TaskError, match='active'):
            service.update_task(task_id, {'status': 'done'})
        await service.cancel_execution(task_id)
        assert stopped.exists()
        assert service.get_task(task_id)['stages'][0]['status'] == 'interrupted'
        with session_scope() as db:
            assert db.get(TaskStage, stage_id).active_task_id is None
    finally:
        if not job.done(): job.cancel(); await asyncio.gather(job, return_exceptions=True)
        service._jobs.pop(stage_id, None)


@pytest.mark.asyncio
async def test_cancel_before_coroutine_start(fleet_db, tmp_path):
    task_id, runtime_id, _ = seed(tmp_path)
    stage_id = execution_stage(task_id, runtime_id, tmp_path)
    async def not_started():
        await asyncio.sleep(100)
    job = asyncio.create_task(not_started())
    service._jobs[stage_id] = job
    try:
        await service.cancel_execution(task_id)
        assert service.get_task(task_id)['stages'][0]['status'] == 'interrupted'
    finally:
        service._jobs.pop(stage_id, None)


def test_recover_interrupted(fleet_db, tmp_path):
    task_id, runtime_id, _ = seed(tmp_path)
    stage_id = execution_stage(task_id, runtime_id, tmp_path)
    service.recover_interrupted()
    assert service.get_task(task_id)['stages'][0]['status'] == 'interrupted'
    with session_scope() as db:
        assert db.get(TaskStage, stage_id).active_task_id is None

@pytest.mark.asyncio
async def test_handoff_api_start_is_pending_then_real_ack(client, tmp_path):
    # Use async service for a single durable event loop (TestClient's POST route uses its own).
    task_id, runtime_id, source_id = seed(tmp_path)
    service.link_session(task_id, source_id)
    executable, _ = fake_agent(tmp_path)
    with session_scope() as db:
        db.add(models.AgentInstallation(runtime_id=runtime_id, agent_id='codex', exec_path=executable))
    draft = target(runtime_id, tmp_path)
    preview = await service.preview_handoff(task_id, draft)
    result = await service.start_handoff(task_id, {**draft, 'preview_hash': preview['preview_hash']})
    assert result['stages'][-1]['status'] == 'starting'
    stage_id = result['stages'][-1]['id']
    job = service._jobs[stage_id]
    try:
        with pytest.raises(service.TaskError, match='active'):
            await service.start_handoff(task_id, {**draft, 'preview_hash': preview['preview_hash']})
        await asyncio.wait_for(asyncio.shield(job), timeout=25)
        result = service.get_task(task_id)
        assert result['stages'][-1]['status'] == 'completed'
        assert result['stages'][0]['session_id'] == source_id
    finally:
        if not job.done(): job.cancel(); await asyncio.gather(job, return_exceptions=True)


@pytest.mark.asyncio
async def test_incremental_return_uses_new_agent_evidence(fleet_db, tmp_path):
    task_id, runtime_id, source_id = seed(tmp_path)
    service.link_session(task_id, source_id)
    with session_scope() as db:
        db.add(TaskStage(task_id=task_id, runtime_id=runtime_id, agent_id='codex', native_session_id='codex1',
            project_path=str(tmp_path), status='completed', output='Codex changed files', started_at=models.utcnow()))
    preview = await service.preview_handoff(task_id, target(runtime_id, tmp_path, agent_id='claude'))
    assert preview['resume_session_id'] == 'source-native'
    assert 'Codex changed files' in preview['prompt']
    assert 'Visible progress' not in preview['prompt']
    new_agent = await service.preview_handoff(task_id, target(runtime_id, tmp_path, agent_id='codex', session_mode='new'))
    assert 'Visible progress' in new_agent['prompt']
    assert 'Codex changed files' in new_agent['prompt']


@pytest.mark.asyncio
async def test_stale_transcript_cache_is_refetched(fleet_db, tmp_path, monkeypatch):
    task_id, runtime_id, source_id = seed(tmp_path)
    service.link_session(task_id, source_id)
    from datetime import timedelta
    with session_scope() as db:
        source = db.get(models.Session, source_id)
        source.updated_at = source.transcript.cached_at + timedelta(seconds=60)
    async def fresh(db, session_id):
        assert db.get(models.Session, session_id).transcript is None
        return {'turns': [{'role': 'assistant', 'content': 'Fresh transcript'}]}
    monkeypatch.setattr(service, 'get_session_transcript', fresh)
    preview = await service.preview_handoff(task_id, target(runtime_id, tmp_path))
    assert 'Fresh transcript' in preview['prompt']
    assert 'Visible progress' not in preview['prompt']


@pytest.mark.asyncio
async def test_missing_transcript_blocks_handoff(fleet_db, tmp_path, monkeypatch):
    task_id, runtime_id, source_id = seed(tmp_path)
    service.link_session(task_id, source_id)
    async def missing(*args): return {'turns': []}
    monkeypatch.setattr(service, 'get_session_transcript', missing)
    with pytest.raises(service.TaskError, match='unavailable'):
        await service.preview_handoff(task_id, target(runtime_id, tmp_path))


@pytest.mark.asyncio
async def test_handoff_stage_startup_and_native_session_linkage(fleet_db, tmp_path):
    """Handoff stage startup immediately links session_id if native session is known/resumed."""
    task_id, runtime_id, source_id = seed(tmp_path)
    service.link_session(task_id, source_id)
    executable, _ = fake_agent(tmp_path, claude=True, resume=True)
    with session_scope() as db:
        db.add(models.AgentInstallation(runtime_id=runtime_id, agent_id='claude', exec_path=executable))

    draft = target(runtime_id, tmp_path, agent_id='claude', session_mode='auto')
    preview = await service.preview_handoff(task_id, draft)
    assert preview['resume_session_id'] == 'source-native'

    result = await service.start_handoff(task_id, {**draft, 'preview_hash': preview['preview_hash']})
    starting_stage = result['stages'][-1]
    assert starting_stage['status'] == 'starting'
    assert starting_stage['native_session_id'] == 'source-native'
    assert starting_stage['session_id'] == source_id

    # Clean up background job
    job = service._jobs.get(starting_stage['id'])
    if job and not job.done():
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_event_arrival_and_session_backfill(fleet_db, tmp_path):
    """Event arrival links session_id immediately if present, or backfills after delayed sync."""
    task_id, runtime_id, source_id = seed(tmp_path)
    stage_id = execution_stage(task_id, runtime_id, tmp_path, agent='codex')

    # Case 1: Session already exists in DB when event arrives
    with session_scope() as db:
        codex_session = models.Session(
            runtime_id=runtime_id, agent_id='codex', session_key='codex-native-1',
            title='Codex 1', project_path=str(tmp_path), updated_at=models.utcnow()
        )
        db.add(codex_session)

    service._update_stage(stage_id, native_session_id='codex-native-1', status='completed')
    task = service.get_task(task_id)
    assert task['stages'][-1]['native_session_id'] == 'codex-native-1'
    assert task['stages'][-1]['session_id'] == codex_session.id

    # Case 2: Session does NOT exist yet when event arrives (delayed sync)
    stage_id_2 = execution_stage(task_id, runtime_id, tmp_path, agent='codex')
    service._update_stage(stage_id_2, native_session_id='codex-delayed-sync', status='completed')
    task = service.get_task(task_id)
    assert task['stages'][-1]['native_session_id'] == 'codex-delayed-sync'
    assert task['stages'][-1]['session_id'] is None

    # Simulate session sync completing later
    with session_scope() as db:
        delayed_session = models.Session(
            runtime_id=runtime_id, agent_id='codex', session_key='codex-delayed-sync',
            title='Codex Delayed', project_path=str(tmp_path), updated_at=models.utcnow()
        )
        db.add(delayed_session)

    # Resolution occurs automatically upon get_task / backfill
    with session_scope() as db:
        backfilled = service.backfill_stage_sessions(db, task_id)
        assert backfilled == 1

    task = service.get_task(task_id)
    assert task['stages'][-1]['session_id'] == delayed_session.id


@pytest.mark.asyncio
async def test_next_hop_handoff_prioritizes_latest_confirmed_session(fleet_db, tmp_path):
    """Next hop handoff prefers the latest valid completed session over stale initial session."""
    task_id, runtime_id, source_id = seed(tmp_path)
    service.link_session(task_id, source_id)

    # Add intermediate completed Codex session
    with session_scope() as db:
        codex_session = models.Session(
            runtime_id=runtime_id, agent_id='codex', session_key='codex-mid-key',
            title='Codex Mid', project_path=str(tmp_path), updated_at=models.utcnow()
        )
        db.add(codex_session); db.flush()
        db.add(models.SessionTranscript(session_id=codex_session.id, turns=[
            {'role': 'user', 'content': 'Do intermediate work'},
            {'role': 'assistant', 'content': 'Codex intermediate turn evidence'}
        ]))
        codex_stage = TaskStage(
            task_id=task_id, runtime_id=runtime_id, session_id=codex_session.id,
            host_name='test-host', agent_id='codex', native_session_id='codex-mid-key',
            project_path=str(tmp_path), kind='handoff', status='completed',
            output='Codex completed turn', started_at=models.utcnow()
        )
        db.add(codex_stage)

    # Preview next hop to Claude (session_mode='new')
    preview = await service.preview_handoff(task_id, target(runtime_id, tmp_path, agent_id='claude', session_mode='new'))
    assert preview['source_session_id'] == codex_session.id
    assert 'Codex intermediate turn evidence' in preview['prompt']
    assert 'Visible progress' not in preview['prompt']
    assert 'test-host / codex' in preview['prompt']


@pytest.mark.asyncio
async def test_defense_against_failed_or_invalid_native_handles(fleet_db, tmp_path):
    """Failed, transcriptless, or invalid handles are strictly barred from resume and source selection."""
    task_id, runtime_id, source_id = seed(tmp_path)
    service.link_session(task_id, source_id)

    with session_scope() as db:
        # Failed stage with handle
        db.add(TaskStage(
            task_id=task_id, runtime_id=runtime_id, agent_id='codex', native_session_id='broken-handle',
            project_path=str(tmp_path), kind='handoff', status='failed', error='Process crashed',
            started_at=models.utcnow()
        ))
        # Stage with invalid / null handle
        db.add(TaskStage(
            task_id=task_id, runtime_id=runtime_id, agent_id='codex', native_session_id='null',
            project_path=str(tmp_path), kind='handoff', status='completed', output='',
            started_at=models.utcnow()
        ))
        # Stage linked to empty transcript session
        empty_session = models.Session(
            runtime_id=runtime_id, agent_id='codex', session_key='empty-session-key',
            project_path=str(tmp_path), message_count=0, updated_at=models.utcnow()
        )
        db.add(empty_session); db.flush()
        db.add(models.SessionTranscript(session_id=empty_session.id, turns=[]))
        db.add(TaskStage(
            task_id=task_id, runtime_id=runtime_id, session_id=empty_session.id,
            agent_id='codex', native_session_id='empty-session-key',
            project_path=str(tmp_path), kind='handoff', status='completed', output='',
            started_at=models.utcnow()
        ))

    # None of these broken stages should be resumable
    preview = await service.preview_handoff(task_id, target(runtime_id, tmp_path, agent_id='codex'))
    assert preview['resume_session_id'] is None

    # Next hop should bypass the failed/empty stages and fall back to the valid source_id
    assert preview['source_session_id'] == source_id
    assert 'Visible progress' in preview['prompt']
