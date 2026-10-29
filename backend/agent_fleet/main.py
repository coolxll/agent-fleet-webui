"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent_fleet.api.routes import router
from agent_fleet.api.daemon_routes import router as daemon_router
from agent_fleet.api.paseo_routes import router as paseo_router
from agent_fleet.api.task_routes import router as task_router
from agent_fleet.api.quota_source_routes import router as quota_source_router
from agent_fleet.api.auth import router as auth_router, APIAuthMiddleware
from agent_fleet.db import create_all, init_engine
from agent_fleet.services.inventory_sync import add_local_runtime
from agent_fleet.services import sqlite_inventory
from agent_fleet.services.scheduler import get_schedule
from agent_fleet.services import task_service, magpie_quota, scan_service
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
    try:
        recovered = scan_service.recover_interrupted_scans()
        if recovered:
            log.info("recovered %d interrupted scan run(s): %s", len(recovered), recovered)
    except Exception as exc:
        log.warning("scan recovery failed: %s", exc)
    try:
        from agent_fleet.services import operation_service
        reconciled = operation_service.reconcile_orphaned_operations()
        if reconciled:
            log.info("reconciled %d orphaned operation(s) on startup", reconciled)
    except Exception as exc:
        log.warning("operation reconciliation failed: %s", exc)
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

app.add_middleware(APIAuthMiddleware)

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

app.include_router(auth_router, prefix="/api")
app.include_router(router, prefix="/api")
app.include_router(daemon_router, prefix="/api")
app.include_router(paseo_router, prefix="/api")
app.include_router(task_router, prefix="/api")
app.include_router(quota_source_router, prefix="/api")


if WEB_DIST.is_dir():
    resolved_web_dist = WEB_DIST.resolve()
    app.mount("/assets", StaticFiles(directory=resolved_web_dist / "assets"), name="assets")
    if (resolved_web_dist / "icons").is_dir():
        app.mount("/icons", StaticFiles(directory=resolved_web_dist / "icons"), name="icons")

    @app.get("/")
    def index():
        index_file = resolved_web_dist / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="Not found")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        clean_path = full_path.replace("\\", "/").lstrip("/")
        try:
            resolved_candidate = (resolved_web_dist / clean_path).resolve()
        except (ValueError, RuntimeError):
            raise HTTPException(status_code=404, detail="Not found")

        if not resolved_candidate.is_relative_to(resolved_web_dist):
            raise HTTPException(status_code=404, detail="Not found")

        rel_path = resolved_candidate.relative_to(resolved_web_dist)
        if any(part.startswith(".") for part in rel_path.parts):
            raise HTTPException(status_code=404, detail="Not found")

        if resolved_candidate.is_file():
            return FileResponse(resolved_candidate)

        index_file = resolved_web_dist / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="Not found")
else:

    @app.get("/")
    def index_placeholder():
        return {
            "service": "agent-fleet",
            "note": "frontend build missing; run npm run build in web/ or use the dev server",
        }
