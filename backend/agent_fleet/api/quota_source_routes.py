"""Explicit read-only account associations; source tokens never reach the API."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent_fleet.services import magpie_quota

router = APIRouter()


class MagpieLink(BaseModel):
    source_account_id: str = Field(min_length=1, max_length=200)


class CollectionSettings(BaseModel):
    enabled: bool
    interval_seconds: int = Field(ge=60, le=86400)


@router.get("/quota-sources/magpie")
def magpie_accounts():
    return magpie_quota.source_accounts()


@router.post("/quota-sources/magpie/link")
async def link_magpie_account(body: MagpieLink):
    try:
        return await magpie_quota.link_account(body.source_account_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except KeyError:
        raise HTTPException(404, "Quota account was removed during linking") from None


@router.get("/quota-collection")
def collection_settings():
    return magpie_quota.collection_settings()


@router.put("/quota-collection")
def update_collection(body: CollectionSettings):
    return magpie_quota.update_collection(body.enabled, body.interval_seconds)
