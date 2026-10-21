"""User task board and explicit handoff actions."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_fleet.services import task_service as service

router = APIRouter(prefix='/tasks', tags=['tasks'])
TaskStatus = Literal['todo', 'in_progress', 'waiting', 'review', 'done']


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default='', max_length=20000)
    status: TaskStatus = 'todo'
    project_path: str = Field(default='', max_length=4096)

    @field_validator('title')
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError('Title is required')
        return value.strip()


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = Field(default=None, max_length=20000)
    status: TaskStatus | None = None
    project_path: str | None = Field(default=None, max_length=4096)

    @field_validator('title')
    @classmethod
    def clean_title(cls, value):
        if value is None or not value.strip():
            raise ValueError('Title is required')
        return value.strip()


class SessionLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=1, max_length=300)


class HandoffPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    runtime_id: str = Field(min_length=1, max_length=300)
    agent_id: Literal['claude', 'codex']
    project_path: str = Field(min_length=1, max_length=4096)
    source_session_id: str | None = Field(default=None, max_length=300)
    notes: str = Field(default='', max_length=20000)
    session_mode: Literal['auto', 'new'] = 'auto'


class HandoffStart(HandoffPreview):
    preview_hash: str = Field(min_length=64, max_length=64)


def invoke(fn, *args):
    try:
        return fn(*args)
    except service.TaskError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


async def invoke_async(fn, *args):
    try:
        return await fn(*args)
    except service.TaskError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('')
def tasks():
    return service.list_tasks()


@router.post('', status_code=201)
def create(body: TaskCreate):
    return invoke(service.create_task, body.model_dump())


@router.get('/{task_id}')
def detail(task_id: str):
    return invoke(service.get_task, task_id)


@router.patch('/{task_id}')
def update(task_id: str, body: TaskUpdate):
    values = body.model_dump(exclude_unset=True)
    if any(value is None for value in values.values()):
        raise HTTPException(422, 'Task fields cannot be null')
    return invoke(service.update_task, task_id, values)


@router.post('/{task_id}/sessions')
def link(task_id: str, body: SessionLink):
    return invoke(service.link_session, task_id, body.session_id)


@router.post('/{task_id}/handoff/preview')
async def preview(task_id: str, body: HandoffPreview):
    return await invoke_async(service.preview_handoff, task_id, body.model_dump())


@router.post('/{task_id}/handoff', status_code=202)
async def handoff(task_id: str, body: HandoffStart):
    return await invoke_async(service.start_handoff, task_id, body.model_dump())


@router.post('/{task_id}/cancel')
async def cancel(task_id: str):
    return await invoke_async(service.cancel_execution, task_id)
