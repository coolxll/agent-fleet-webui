"""Explicit, read-only Paseo observations. GET never connects to hosts."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services import paseo_service
from agent_fleet.settings import get_settings

router = APIRouter(tags=["paseo"])


class RefreshRequest(BaseModel):
    runtime_ids: list[str] | None = Field(default=None, max_length=200)


@router.get("/paseo")
def list_paseo(runtime_id: str | None = None):
    return paseo_service.list_observations([runtime_id] if runtime_id else None)


@router.post("/paseo/refresh")
async def refresh_paseo(body: RefreshRequest):
    if body.runtime_ids is not None:
        with session_scope() as s:
            found = set(s.scalars(select(models.Runtime.id).where(models.Runtime.id.in_(body.runtime_ids))))
        if found != set(body.runtime_ids):
            raise HTTPException(404, "Unknown runtime")
    return await paseo_service.refresh(body.runtime_ids, get_settings())
