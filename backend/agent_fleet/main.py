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
from agent_fleet.db import create_all, init_engine
from agent_fleet.services.inventory_sync import add_local_runtime, sync_inventory
from agent_fleet.settings import ROOT, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agent_fleet")

WEB_DIST = ROOT / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_engine(settings.db_path)
    create_all()
    if settings.include_local_runtime:
        try:
            add_local_runtime(settings)
        except Exception as exc:
            log.warning("local runtime setup failed: %s", exc)
    try:
        result = sync_inventory(settings)
        log.info("inventory sync: %s", result)
    except Exception as exc:
        log.warning("inventory sync failed: %s", exc)
    yield


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


@app.get("/api/health")
def api_health():
    return {"ok": True}


if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

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
