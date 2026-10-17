"""HTTP API. Read-only views plus scan triggers."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from agent_fleet import models
from agent_fleet.adapters.registry import agent_ids, agent_meta
from agent_fleet.db import session_scope
from agent_fleet.services import persist as _persist  # noqa: F401
from agent_fleet.services import scan_service
from agent_fleet.services.inventory_sync import add_local_runtime, sync_inventory
from agent_fleet.settings import get_settings

router = APIRouter()

STALE_GRACE = 60  # seconds of slack before a snapshot counts as stale


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _age(dt):
    if not dt:
        return None
    return int((datetime.utcnow() - dt).total_seconds())


def _stale(rt, ttl):
    if not rt.last_success_at:
        return None
    return _age(rt.last_success_at) > (ttl + STALE_GRACE)


# --------------------------------------------------------------------------
# serializers
# --------------------------------------------------------------------------


def _rt_summary(s, rt, ttl):
    installed = 0
    for inst in rt.installations:
        if inst.detected:
            installed += 1
    return {
        "id": rt.id,
        "host_id": rt.host_id,
        "host": rt.host.name if rt.host else None,
        "key": rt.key,
        "kind": rt.kind,
        "label": rt.label,
        "os_name": rt.os_name,
        "arch": rt.arch,
        "home_dir": rt.home_dir,
        "collect_user": rt.collect_user,
        "transport": rt.transport,
        "ssh_target": rt.ssh_alias or rt.ssh_host,
        "wsl_distro": rt.wsl_distro,
        "enabled": rt.enabled,
        "reachable": rt.reachable,
        "last_seen_at": _iso(rt.last_seen_at),
        "last_success_at": _iso(rt.last_success_at),
        "last_scan_at": _iso(rt.last_scan_at),
        "last_error": rt.last_error,
        "snapshot_age_s": _age(rt.last_success_at),
        "stale": _stale(rt, ttl),
        "agents_installed": installed,
    }


def _inst_detail(inst, include_config=True):
    out = {
        "id": inst.id,
        "runtime_id": inst.runtime_id,
        "agent": inst.agent_id,
        "status": inst.status,
        "detected": inst.detected,
        "version": inst.version,
        "version_raw": inst.version_raw,
        "exec_path": inst.exec_path,
        "install_method": inst.install_method,
        "config_dir": inst.config_dir,
        "detected_at": _iso(inst.detected_at),
        "error": inst.error,
        "config_status": inst.config_status,
    }
    if include_config:
        cfg = inst.config
        if cfg is not None:
            out["config"] = {
                "id": cfg.id,
                "resolution": cfg.resolution,
                "parse_status": cfg.parse_status,
                "model": cfg.model,
                "provider": cfg.model_provider,
                "base_url": cfg.base_url,
                "base_url_masked": cfg.base_url_masked,
                "mcp_servers": cfg.mcp_servers,
                "skills": cfg.skills,
                "settings": cfg.settings,
                "unsupported_fields": cfg.unsupported_fields,
                "sanitized": cfg.sanitized,
                "warnings": cfg.warnings,
                "collected_at": _iso(cfg.collected_at),
            }
        else:
            out["config"] = None
        out["sources"] = [
            {
                "id": s.id,
                "scope": s.scope,
                "path": s.path,
                "format": s.format,
                "kind": s.kind,
                "present": s.present,
                "readable": s.readable,
                "size_bytes": s.size_bytes,
                "status": s.status,
                "error": s.error,
                "captured_at": _iso(s.captured_at),
            }
            for s in sorted(inst.sources, key=lambda x: x.path)
        ]
    return out


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------


@router.get("/health")
def health():
    return {"ok": True, "service": "agent-fleet", "ts": datetime.utcnow().isoformat() + "Z"}


@router.get("/agents")
def agents():
    return {"agents": agent_meta()}


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------


@router.get("/hosts")
def list_hosts():
    settings = get_settings()
    with session_scope() as s:
        hosts = s.execute(select(models.Host).order_by(models.Host.name)).scalars().all()
        out = []
        for h in hosts:
            rts = s.execute(
                select(models.Runtime).where(models.Runtime.host_id == h.id)
            ).scalars().all()
            out.append(
                {
                    "id": h.id,
                    "name": h.name,
                    "hostname": h.hostname,
                    "source": h.inventory_source,
                    "group": h.group,
                    "tags": h.tags or {},
                    "runtimes": [
                        _rt_summary(s, r, settings.scan_ttl_seconds) for r in rts
                    ],
                }
            )
        return {"hosts": out}


@router.get("/runtimes")
def list_runtimes(enabled_only: bool = Query(False)):
    settings = get_settings()
    with session_scope() as s:
        q = select(models.Runtime)
        if enabled_only:
            q = q.where(models.Runtime.enabled.is_(True))
        rts = s.execute(q.order_by(models.Runtime.key)).scalars().all()
        return {
            "runtimes": [_rt_summary(s, r, settings.scan_ttl_seconds) for r in rts],
            "stale_after_s": settings.scan_ttl_seconds,
        }


@router.post("/runtimes/{runtime_id}/enabled")
def set_runtime_enabled(runtime_id: str, payload: dict):
    enabled = bool(payload.get("enabled", True))
    with session_scope() as s:
        rt = s.get(models.Runtime, runtime_id)
        if rt is None:
            raise HTTPException(404, "runtime not found")
        rt.enabled = enabled
        s.commit()
    return {"ok": True, "enabled": enabled}


@router.post("/inventory/sync")
def inventory_sync():
    settings = get_settings()
    result = sync_inventory(settings)
    if settings.include_local_runtime:
        add_local_runtime(settings)
    return {"ok": True, **result}


# --------------------------------------------------------------------------
# dashboard views
# --------------------------------------------------------------------------


@router.get("/overview")
def overview():
    settings = get_settings()
    ttl = settings.scan_ttl_seconds
    with session_scope() as s:
        rts = s.execute(select(models.Runtime)).scalars().all()
        online = [r for r in rts if r.reachable is True]
        offline = [r for r in rts if r.reachable is False]
        unknown = [r for r in rts if r.reachable is None]
        per_agent = {}
        installs = 0
        last_ok = None
        for r in rts:
            for i in r.installations:
                if i.detected:
                    installs += 1
                    per_agent[i.agent_id] = per_agent.get(i.agent_id, 0) + 1
            if r.last_success_at and (last_ok is None or r.last_success_at > last_ok):
                last_ok = r.last_success_at
        runs = s.execute(
            select(models.ScanRun).order_by(models.ScanRun.started_at.desc()).limit(5)
        ).scalars().all()
        return {
            "totals": {
                "runtimes": len(rts),
                "hosts": len({r.host_id for r in rts}),
                "online": len(online),
                "offline": len(offline),
                "unknown": len(unknown),
                "installations": installs,
            },
            "agents": [
                {"id": a, "installations": per_agent.get(a, 0)} for a in agent_ids()
            ],
            "last_successful_scan": _iso(last_ok),
            "recent_runs": [
                {
                    "id": r.id,
                    "status": r.status,
                    "started": _iso(r.started_at),
                    "finished": _iso(r.finished_at),
                    "total": r.total,
                    "succeeded": r.succeeded,
                    "failed": r.failed,
                }
                for r in runs
            ],
            "stale_after_s": ttl,
        }


@router.get("/matrix")
def matrix():
    settings = get_settings()
    ttl = settings.scan_ttl_seconds
    with session_scope() as s:
        rts = s.execute(
            select(models.Runtime).order_by(models.Runtime.key)
        ).scalars().all()
        ids = [r.id for r in rts]
        rows = s.execute(
            select(models.AgentInstallation).where(
                models.AgentInstallation.runtime_id.in_(ids)
            )
        ).scalars().all() if ids else []
        by_rt = {}
        for i in rows:
            by_rt.setdefault(i.runtime_id, {})[i.agent_id] = i
        columns = []
        for r in rts:
            columns.append(
                {
                    "runtime": _rt_summary(s, r, ttl),
                    "cells": {
                        a: _matrix_cell(by_rt.get(r.id, {}).get(a), r)
                        for a in agent_ids()
                    },
                }
            )
        return {
            "agents": agent_meta(),
            "columns": columns,
            "stale_after_s": ttl,
        }


def _matrix_cell(inst, rt):
    if rt.last_scan_at is None:
        return {"state": "not_scanned", "installation_id": None, "version": None}
    if rt.reachable is False and rt.last_error:
        return {
            "state": "scan_failed",
            "error": rt.last_error,
            "installation_id": inst.id if inst else None,
            "version": inst.version if inst and inst.detected else None,
            "last_success": _iso(rt.last_success_at),
        }
    if inst is None:
        return {"state": "unknown", "installation_id": None, "version": None}
    if inst.status == "installed":
        return {
            "state": "installed",
            "installation_id": inst.id,
            "version": inst.version,
            "config_status": inst.config_status,
        }
    if inst.status == "config_only":
        return {
            "state": "config_only",
            "installation_id": inst.id,
            "version": None,
            "config_status": inst.config_status,
        }
    if inst.status == "not_installed":
        return {"state": "not_installed", "installation_id": inst.id, "version": None}
    if inst.status == "error":
        return {
            "state": "error",
            "installation_id": inst.id,
            "error": inst.error,
            "version": inst.version,
        }
    return {"state": "unknown", "installation_id": inst.id, "version": inst.version}


# --------------------------------------------------------------------------
# inspector
# --------------------------------------------------------------------------


@router.get("/installations")
def list_installations(
    runtime_id: str = Query(None),
    agent_id: str = Query(None),
    detected_only: bool = Query(False),
):
    with session_scope() as s:
        q = select(models.AgentInstallation)
        if runtime_id:
            q = q.where(models.AgentInstallation.runtime_id == runtime_id)
        if agent_id:
            q = q.where(models.AgentInstallation.agent_id == agent_id)
        if detected_only:
            q = q.where(models.AgentInstallation.detected.is_(True))
        rows = s.execute(q).scalars().all()
        out = []
        for i in rows:
            item = _inst_detail(i, include_config=False)
            rt = s.get(models.Runtime, i.runtime_id)
            item["runtime"] = {
                "id": i.runtime_id,
                "key": rt.key if rt else None,
                "kind": rt.kind if rt else None,
                "host": rt.host.name if rt and rt.host else None,
                "collect_user": rt.collect_user if rt else None,
            }
            out.append(item)
        return {"installations": out}


@router.get("/installations/{inst_id}")
def get_installation(inst_id: str):
    with session_scope() as s:
        inst = s.get(models.AgentInstallation, inst_id)
        if inst is None:
            raise HTTPException(404, "installation not found")
        item = _inst_detail(inst)
        rt = s.get(models.Runtime, inst.runtime_id)
        item["runtime"] = {
            "id": inst.runtime_id,
            "key": rt.key if rt else None,
            "kind": rt.kind if rt else None,
            "label": rt.label if rt else None,
            "host": rt.host.name if rt and rt.host else None,
            "collect_user": rt.collect_user if rt else None,
            "os_name": rt.os_name if rt else None,
            "last_success_at": _iso(rt.last_success_at) if rt else None,
            "reachable": rt.reachable if rt else None,
        }
        return item


@router.get("/snapshots")
def list_snapshots(runtime_id: str = Query(None), limit: int = Query(50)):
    with session_scope() as s:
        q = select(models.Snapshot).order_by(models.Snapshot.created_at.desc()).limit(limit)
        if runtime_id:
            q = q.where(models.Snapshot.runtime_id == runtime_id)
        rows = s.execute(q).scalars().all()
        return {
            "snapshots": [
                {
                    "id": r.id,
                    "runtime_id": r.runtime_id,
                    "created_at": _iso(r.created_at),
                    "agents_found": r.agents_found,
                    "payload": r.payload,
                }
                for r in rows
            ]
        }


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------


class ScanRequest(BaseModel):
    runtime_ids: list[str] = []
    all: bool = False


@router.post("/scan")
async def start_scan(req: ScanRequest):
    settings = get_settings()
    with session_scope() as s:
        if req.all or not req.runtime_ids:
            rts = s.execute(
                select(models.Runtime).where(models.Runtime.enabled.is_(True))
            ).scalars().all()
            ids = [r.id for r in rts]
        else:
            wanted = set(req.runtime_ids)
            rts = s.execute(select(models.Runtime)).scalars().all()
            ids = [r.id for r in rts if r.id in wanted]
            missing = wanted - set(ids)
            for m in missing:
                raise HTTPException(404, f"runtime not found: {m}")
    if not ids:
        raise HTTPException(400, "no enabled runtimes to scan")
    run_id = await scan_service.start_scan(ids, "manual", settings)
    return {"run_id": run_id, "total": len(ids)}


@router.get("/scan/active")
def active_scans():
    return {"runs": scan_service.active_runs()}


@router.get("/scan/{run_id}")
def get_scan(run_id: str):
    with session_scope() as s:
        run = s.get(models.ScanRun, run_id)
        if run is None:
            raise HTTPException(404, "scan run not found")
        tasks = s.execute(
            select(models.ScanTask).where(models.ScanTask.run_id == run_id)
        ).scalars().all()
        live = scan_service.run_status(run_id) or {}
        out = {
            "id": run.id,
            "status": run.status,
            "triggered_by": run.triggered_by,
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "total": run.total,
            "succeeded": run.succeeded,
            "failed": run.failed,
            "concurrency": run.concurrency,
            "live": {
                "done": live.get("done", 0),
                "ok": live.get("ok", 0),
                "failed": live.get("failed", 0),
                "status": live.get("status"),
            },
            "tasks": [
                {
                    "id": t.id,
                    "runtime_id": t.runtime_id,
                    "status": t.status,
                    "duration_ms": t.duration_ms,
                    "agents_found": t.agents_found,
                    "error": t.error,
                    "started_at": _iso(t.started_at),
                    "finished_at": _iso(t.finished_at),
                }
                for t in tasks
            ],
        }
        return out


@router.get("/scans")
def list_scans(limit: int = Query(20)):
    with session_scope() as s:
        runs = s.execute(
            select(models.ScanRun).order_by(models.ScanRun.started_at.desc()).limit(limit)
        ).scalars().all()
        return {
            "runs": [
                {
                    "id": r.id,
                    "status": r.status,
                    "started_at": _iso(r.started_at),
                    "finished_at": _iso(r.finished_at),
                    "total": r.total,
                    "succeeded": r.succeeded,
                    "failed": r.failed,
                }
                for r in runs
            ]
        }
