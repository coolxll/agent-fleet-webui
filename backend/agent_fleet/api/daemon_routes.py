"""Explicit, read-only Daemon & Supervisor observations (Paseo, Herdr)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services import herdr_service, paseo_service
from agent_fleet.settings import get_settings

router = APIRouter(tags=["daemons"])


class RefreshRequest(BaseModel):
    runtime_ids: list[str] | None = Field(default=None, max_length=200)


class RefreshDaemonsRequest(BaseModel):
    runtime_ids: list[str] | None = Field(default=None, max_length=200)
    services: list[str] | None = Field(default=None, max_length=10)


def _validate_runtimes(runtime_ids: list[str] | None):
    if runtime_ids is not None:
        with session_scope() as s:
            found = set(s.scalars(select(models.Runtime.id).where(models.Runtime.id.in_(runtime_ids))))
        if found != set(runtime_ids):
            raise HTTPException(404, "Unknown runtime")


@router.get("/herdr")
def list_herdr(runtime_id: str | None = None):
    return herdr_service.list_observations([runtime_id] if runtime_id else None)


@router.post("/herdr/refresh")
async def refresh_herdr(body: RefreshRequest):
    _validate_runtimes(body.runtime_ids)
    return await herdr_service.refresh(body.runtime_ids, get_settings())


@router.get("/daemons")
def list_daemons(runtime_id: str | None = None):
    r_ids = [runtime_id] if runtime_id else None
    return {
        "paseo": paseo_service.list_observations(r_ids).get("items", []),
        "herdr": herdr_service.list_observations(r_ids).get("items", []),
    }


@router.post("/daemons/refresh")
async def refresh_daemons(body: RefreshDaemonsRequest):
    _validate_runtimes(body.runtime_ids)
    settings = get_settings()
    services = set(body.services) if body.services else {"paseo", "herdr"}
    paseo_items = []
    herdr_items = []
    if "paseo" in services:
        paseo_res = await paseo_service.refresh(body.runtime_ids, settings)
        paseo_items = paseo_res.get("items", [])
    else:
        paseo_items = paseo_service.list_observations(body.runtime_ids).get("items", [])
    if "herdr" in services:
        herdr_res = await herdr_service.refresh(body.runtime_ids, settings)
        herdr_items = herdr_res.get("items", [])
    else:
        herdr_items = herdr_service.list_observations(body.runtime_ids).get("items", [])
    return {
        "paseo": paseo_items,
        "herdr": herdr_items,
    }
