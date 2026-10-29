"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent_fleet.api.routes import router
from agent_fleet.api.daemon_routes import router as daemon_router
from agent_fleet.api.paseo_routes import router as paseo_router
from agent_fleet.api.task_routes import router as task_router
from agent_fleet.api.quota_source_routes import router as quota_source_router
from agent_fleet.db import create_all, init_engine
from agent_fleet.services.inventory_sync import add_local_runtime
from agent_fleet.services import sqlite_inventory
from agent_fleet.services.scheduler import get_schedule
from agent_fleet.services import task_service, magpie_quota
from agent_fleet.settings import ROOT, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agent_fleet")

WEB_DIST = ROOT / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_engine(settings.db_path)
    create_all()
    try:
        migrated = sqlite_inventory.seed_from_legacy_files_if_needed()
        if migrated.get("created", 0) > 0 or migrated.get("updated", 0) > 0:
            log.info("sqlite inventory migration: %s", migrated)
    except Exception as exc:
        log.warning("sqlite inventory migration failed: %s", exc)
    task_service.recover_interrupted()
    if settings.include_local_runtime:
        try:
            add_local_runtime(settings)
        except Exception as exc:
            log.warning("local runtime setup failed: %s", exc)
    try:
        schedule = get_schedule()
        if schedule.enabled:
            schedule.start()
            log.info("scheduled scanning enabled (every %ss)", schedule.interval_seconds)
    except Exception as exc:
        log.warning("scheduler start failed: %s", exc)
    quota_collector = magpie_quota.Collector()
    quota_collector.start()
    try:
        yield
    finally:
        await quota_collector.stop()
        await task_service.shutdown()


app = FastAPI(title="Agent Fleet Explorer", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(daemon_router, prefix="/api")
app.include_router(paseo_router, prefix="/api")
app.include_router(task_router, prefix="/api")
app.include_router(quota_source_router, prefix="/api")


if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")
    if (WEB_DIST / "icons").is_dir():
        app.mount("/icons", StaticFiles(directory=WEB_DIST / "icons"), name="icons")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIST / "index.html")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = WEB_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
else:

    @app.get("/")
    def index_placeholder():
        return {
            "service": "agent-fleet",
            "note": "frontend build missing; run npm run build in web/ or use the dev server",
        }
