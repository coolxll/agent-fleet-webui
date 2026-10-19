"""HTTP API. Read-only views plus scan triggers."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select

from agent_fleet import models
from agent_fleet.adapters.registry import agent_ids, agent_meta
from agent_fleet.db import session_scope
from agent_fleet.services import diff
from agent_fleet.services import persist as _persist  # noqa: F401
from agent_fleet.services import scan_service
from agent_fleet.services.export import build_export
from agent_fleet.services.updates import (
    effective_latest,
    latest_available,
    version_state,
    version_state_for,
)
from agent_fleet.services.scheduler import get_schedule
from agent_fleet.services.inventory_sync import add_local_runtime, sync_inventory
from agent_fleet.settings import get_settings

router = APIRouter()

STALE_GRACE = 60  # seconds of slack before a snapshot counts as stale

_iso = models.iso_utc


def _age(dt):
    if not dt:
        return None
    return int((datetime.utcnow() - dt).total_seconds())


def _visible(s, include_disabled: bool):
    """Runtime ids that should appear in fleet views."""
    q = select(models.Runtime)
    if not include_disabled:
        q = q.join(models.Host).where(models.Host.enabled.is_(True))
    return s.execute(q).scalars().all()


def _stale(rt, ttl):
    if not rt.last_success_at:
        return None
    return _age(rt.last_success_at) > (ttl + STALE_GRACE)


# --------------------------------------------------------------------------
# serializers
# --------------------------------------------------------------------------


def _rt_summary(s, rt, ttl):
    installed = 0
    agent_details = []
    for inst in rt.installations:
        if inst.detected:
            installed += 1
            latest_info = latest_available(inst.agent_id)
            latest_ver = effective_latest(inst.version, latest_info)
            agent_details.append(
                {
                    "id": inst.id,
                    "agent": inst.agent_id,
                    "version": inst.version,
                    "status": inst.status,
                    "version_state": (
                        version_state(inst.version, latest_ver)
                        if inst.status == "installed"
                        else "unknown"
                    ),
                    "latest_available": latest_ver,
                    "package": latest_info.get("package"),
                    "source": latest_info.get("source"),
                    "release_url": latest_info.get("release_url"),
                }
            )
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
        "agent_details": agent_details,
    }


def _config_summary(inst):
    cfg = getattr(inst, "config", None)
    if cfg is None:
        return None
    return {
        "model": cfg.model,
        "provider": cfg.model_provider,
        "base_url": cfg.base_url,
        "parse_status": cfg.parse_status,
        "resolution": cfg.resolution,
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
        "config_summary": _config_summary(inst),
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


@router.get("/updates")
def list_updates():
    return {aid: latest_available(aid) for aid in agent_ids()}


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------


@router.get("/machines/{host_id}")
def get_machine(host_id: str):
    """One machine, its runtimes, and every agent install on it.

    This is the machine-first view: the UI lands here after a host card click.
    """
    settings = get_settings()
    with session_scope() as s:
        host = s.get(models.Host, host_id)
        if host is None:
            raise HTTPException(404, "host not found")
        runtimes = []
        for rt in sorted(host.runtimes, key=lambda r: (r.key != "native", r.key)):
            installs = s.execute(
                select(models.AgentInstallation)
                .where(models.AgentInstallation.runtime_id == rt.id)
                .order_by(models.AgentInstallation.agent_id)
            ).scalars().all()
            runtimes.append(
                {
                    **_rt_summary(s, rt, settings.scan_ttl_seconds),
                    "installations": [
                        {
                            "id": i.id,
                            "agent": i.agent_id,
                            "status": i.status,
                            "detected": i.detected,
                            "version": i.version,
                            "exec_path": i.exec_path,
                            "install_method": i.install_method,
                            "config_dir": i.config_dir,
                            "config_status": i.config_status,
                            "error": i.error,
                            "config_summary": _config_summary(i),
                            "version_state": (
                                version_state_for(i.version, latest_available(i.agent_id))
                                if i.status == "installed"
                                else "unknown"
                            ),
                            "latest_available": effective_latest(
                                i.version, latest_available(i.agent_id)
                            ),
                            "package": latest_available(i.agent_id).get("package"),
                            "source": latest_available(i.agent_id).get("source"),
                            "release_url": latest_available(i.agent_id).get(
                                "release_url"
                            ),
                        }
                        for i in installs
                    ],
                }
            )
        detected = sum(
            1
            for r in runtimes
            for i in r["installations"]
            if i["detected"]
        )
        return {
            "id": host.id,
            "name": host.name,
            "hostname": host.hostname,
            "source": host.inventory_source,
            "group": host.group,
            "tags": host.tags or {},
            "enabled": host.enabled,
            "runtimes": runtimes,
            "agents_detected": detected,
        }


@router.get("/hosts")
def list_hosts(include_disabled: bool = Query(False)):
    settings = get_settings()
    with session_scope() as s:
        q = select(models.Host).order_by(models.Host.name)
        if not include_disabled:
            q = q.where(models.Host.enabled.is_(True))
        hosts = s.execute(q).scalars().all()
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
                    "enabled": h.enabled,
                    "runtimes": [
                        {
                            **_rt_summary(s, r, settings.scan_ttl_seconds),
                            "agents": sorted(
                                i.agent_id for i in r.installations if i.detected
                            ),
                        }
                        for r in rts
                    ],
                }
            )
        return {"hosts": out, "include_disabled": include_disabled}


@router.post("/hosts/{host_id}/enabled")
def set_host_enabled(host_id: str, payload: dict):
    enabled = bool(payload.get("enabled", True))
    with session_scope() as s:
        host = s.get(models.Host, host_id)
        if host is None:
            raise HTTPException(404, "host not found")
        host.enabled = enabled
        for rt in host.runtimes:
            rt.enabled = enabled if not enabled else rt.enabled
        s.commit()
    return {"ok": True, "enabled": enabled, "host": host.name}


@router.get("/runtimes")
def list_runtimes(enabled_only: bool = Query(False), include_disabled_hosts: bool = Query(False)):
    settings = get_settings()
    with session_scope() as s:
        q = select(models.Runtime)
        if enabled_only:
            q = q.where(models.Runtime.enabled.is_(True))
        if not include_disabled_hosts:
            q = q.join(models.Host).where(models.Host.enabled.is_(True))
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


@router.delete("/runtimes/{runtime_id}")
def delete_runtime(runtime_id: str):
    """Remove a runtime and everything collected from it.

    Used for rows that turned out to be duplicates (a WSL distro already
    reachable through its own SSH endpoint) or that you no longer want.
    """
    with session_scope() as s:
        rt = s.get(models.Runtime, runtime_id)
        if rt is None:
            raise HTTPException(404, "runtime not found")
        label = f"{rt.host.name if rt.host else '?'}/{rt.key}"
        # children that have no ON DELETE CASCADE of their own
        s.execute(
            delete(models.AgentConfiguration).where(
                models.AgentConfiguration.installation_id.in_(
                    select(models.AgentInstallation.id).where(
                        models.AgentInstallation.runtime_id == runtime_id
                    )
                )
            )
        )
        s.execute(delete(models.Snapshot).where(models.Snapshot.runtime_id == runtime_id))
        s.execute(delete(models.ScanTask).where(models.ScanTask.runtime_id == runtime_id))
        s.delete(rt)  # installations and their sources cascade
        s.commit()
    return {"ok": True, "deleted": label}


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
        rts = _visible(s, include_disabled=False)
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
        rts = sorted(_visible(s, include_disabled=False), key=lambda r: r.key)
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
    include_disabled_hosts: bool = Query(False),
):
    with session_scope() as s:
        if not include_disabled_hosts and not runtime_id:
            visible = [r.id for r in _visible(s, include_disabled=False)]
            q = select(models.AgentInstallation).where(
                models.AgentInstallation.runtime_id.in_(visible)
            )
        else:
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


@router.post("/installations/{inst_id}/upgrade")
async def upgrade_installation_route(inst_id: str, wait: bool = Query(False)):
    from agent_fleet.services.action_service import (
        start_upgrade_task,
        upgrade_agent_installation,
    )

    try:
        if wait:
            return await upgrade_agent_installation(inst_id)
        return start_upgrade_task(inst_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Upgrade execution error: {exc}")


@router.post("/runtimes/{runtime_id}/agents/{agent_id}/upgrade")
async def upgrade_runtime_agent_route(
    runtime_id: str, agent_id: str, wait: bool = Query(False)
):
    from agent_fleet.services.action_service import (
        start_upgrade_task,
        upgrade_agent_installation,
    )

    with session_scope() as s:
        inst = s.execute(
            select(models.AgentInstallation).where(
                models.AgentInstallation.runtime_id == runtime_id,
                models.AgentInstallation.agent_id == agent_id,
            )
        ).scalar_one_or_none()
        if not inst:
            raise HTTPException(
                404, f"No installation of {agent_id} found on runtime {runtime_id}"
            )
        inst_id = inst.id

    try:
        if wait:
            return await upgrade_agent_installation(inst_id)
        return start_upgrade_task(inst_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Upgrade execution error: {exc}")


@router.get("/upgrades/{task_id}")
def get_upgrade_task_route(task_id: str):
    from agent_fleet.services.action_service import get_upgrade_task

    task = get_upgrade_task(task_id)
    if not task:
        raise HTTPException(404, "Upgrade task not found")
    return task


@router.get("/installations/{inst_id}/upgrade/active")
def get_active_upgrade_route(inst_id: str):
    from agent_fleet.services.action_service import get_active_task_for_installation

    return {"active_task": get_active_task_for_installation(inst_id)}


@router.get("/upgrades")
def list_upgrades_route(limit: int = Query(30)):
    from agent_fleet.services.action_service import list_upgrade_tasks

    return {"tasks": list_upgrade_tasks(limit)}


class BatchUpgradeRequest(BaseModel):
    installation_ids: list[str]


@router.post("/upgrades/batch")
def batch_upgrade_route(payload: BatchUpgradeRequest):
    from agent_fleet.services.action_service import start_batch_upgrade

    tasks = start_batch_upgrade(payload.installation_ids)
    return {"tasks": tasks, "queued": len(tasks)}


@router.post("/machines/{host_id}/upgrade-all")
def upgrade_machine_all_route(host_id: str):
    from agent_fleet.services.action_service import find_outdated_installations, start_batch_upgrade

    outdated_ids = find_outdated_installations(host_id=host_id)
    tasks = start_batch_upgrade(outdated_ids)
    return {"tasks": tasks, "queued": len(tasks), "host_id": host_id}


@router.post("/agents/{agent_id}/upgrade-all")
def upgrade_agent_all_route(agent_id: str):
    from agent_fleet.services.action_service import find_outdated_installations, start_batch_upgrade

    outdated_ids = find_outdated_installations(agent_id=agent_id)
    tasks = start_batch_upgrade(outdated_ids)
    return {"tasks": tasks, "queued": len(tasks), "agent_id": agent_id}


@router.post("/fleet/upgrade-all")
def upgrade_fleet_all_route():
    from agent_fleet.services.action_service import find_outdated_installations, start_batch_upgrade

    outdated_ids = find_outdated_installations()
    tasks = start_batch_upgrade(outdated_ids)
    return {"tasks": tasks, "queued": len(tasks)}


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


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str):
    with session_scope() as s:
        snap = s.get(models.Snapshot, snapshot_id)
        if snap is None:
            raise HTTPException(404, "snapshot not found")
        rt = s.get(models.Runtime, snap.runtime_id)
        return {
            "id": snap.id,
            "runtime_id": snap.runtime_id,
            "runtime": {
                "id": snap.runtime_id,
                "host": rt.host.name if rt and rt.host else None,
                "key": rt.key if rt else None,
                "kind": rt.kind if rt else None,
            },
            "created_at": _iso(snap.created_at),
            "agents_found": snap.agents_found,
            "payload": snap.payload,
        }


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


@router.get("/diff/installations")
def diff_installations(a: str = Query(...), b: str = Query(...)):
    """Compare two installations (same agent on different machines)."""
    with session_scope() as s:
        ia = s.get(models.AgentInstallation, a)
        ib = s.get(models.AgentInstallation, b)
        if ia is None or ib is None:
            raise HTTPException(404, "one or both installations not found")
        result = diff.compare_installations(ia, ib)
        result["same_agent"] = ia.agent_id == ib.agent_id
        for side in ("a", "b"):
            rt = s.get(models.Runtime, result[side]["runtime_id"])
            result[side]["runtime"] = {
                "host": rt.host.name if rt and rt.host else None,
                "key": rt.key if rt else None,
                "kind": rt.kind if rt else None,
                "collect_user": rt.collect_user if rt else None,
            }
        result["same_agent"] = ia.agent_id == ib.agent_id
        return result


@router.get("/diff/snapshots")
def diff_snapshots(a: str = Query(...), b: str = Query(...)):
    """Compare two snapshots (history, or two different runtimes)."""
    with session_scope() as s:
        sa = s.get(models.Snapshot, a)
        sb = s.get(models.Snapshot, b)
        if sa is None or sb is None:
            raise HTTPException(404, "one or both snapshots not found")
        result = diff.compare_snapshots(sa, sb)
        for side, snap in (("a", sa), ("b", sb)):
            rt = s.get(models.Runtime, snap.runtime_id)
            result[side]["runtime"] = {
                "host": rt.host.name if rt and rt.host else None,
                "key": rt.key if rt else None,
                "kind": rt.kind if rt else None,
            }
            result[side]["created_at"] = _iso(snap.created_at)
        return result


@router.get("/agent-view/{agent_id}")
def agent_view(agent_id: str, include_disabled_hosts: bool = Query(False)):
    """Agent-first view: one agent across every machine.

    Answers two questions at a glance: are the versions current, and are the
    configurations consistent.
    """
    from agent_fleet.adapters.registry import get_adapter as _get_adapter
    from agent_fleet.services.updates import effective_latest, latest_available

    if agent_id not in agent_ids():
        raise HTTPException(404, f"unknown agent: {agent_id}")

    with session_scope() as s:
        if include_disabled_hosts:
            runtimes = s.execute(select(models.Runtime)).scalars().all()
        else:
            runtimes = _visible(s, include_disabled=False)
        by_id = {r.id: r for r in runtimes}
        rows = s.execute(
            select(models.AgentInstallation).where(
                models.AgentInstallation.agent_id == agent_id
            )
        ).scalars().all()
        installations = []
        for i in rows:
            rt = by_id.get(i.runtime_id)
            if rt is None:
                continue
            cfg = getattr(i, "config", None)
            installations.append(
                {
                    "id": i.id,
                    "host": rt.host.name if rt.host else None,
                    "runtime_key": rt.key,
                    "kind": rt.kind,
                    "os_name": rt.os_name,
                    "arch": rt.arch,
                    "ssh_target": rt.ssh_alias or rt.ssh_host or (rt.host.name if rt.host else None),
                    "collect_user": rt.collect_user,
                    "runtime_id": i.runtime_id,
                    "status": i.status,
                    "detected": i.detected,
                    "version": i.version,
                    "install_method": i.install_method,
                    "config_status": i.config_status,
                    "config_summary": _config_summary(i),
                    "collected_at": _iso(cfg.collected_at) if cfg else None,
                }
            )

    detected = [i for i in installations if i["detected"]]
    currency = latest_available(agent_id)
    latest = currency.get("latest_available")
    for i in detected:
        # Per install, because a machine riding a prerelease channel is
        # compared against that channel rather than stable `latest`.
        effective = effective_latest(i["version"], currency)
        i["version_state"] = (
            version_state(i["version"], effective)
            if i["status"] == "installed"
            else "unknown"
        )
        i["latest_available"] = effective
        i["package"] = currency.get("package")
        i["source"] = currency.get("source")

    # config consistency: group detected installs by their declared triple
    from collections import Counter
    model_counts = Counter(
        (i["config_summary"] or {}).get("model")
        for i in detected
        if i.get("config_summary") and (i["config_summary"] or {}).get("model")
    )

    groups: dict = {}
    for i in detected:
        cs = i["config_summary"] or {}
        signature = " / ".join(
            str(cs.get(k) or "?") for k in ("model", "provider", "base_url")
        )
        groups.setdefault(signature, []).append(i)

    def _group_sort_key(item):
        sig, insts = item
        count = len(insts)
        # Check model popularity across the fleet
        m = (insts[0].get("config_summary") or {}).get("model") if insts else None
        m_pop = model_counts.get(m, 0)
        # Completeness: how many fields are defined instead of '?'
        completeness = sum(1 for part in sig.split(" / ") if part != "?")
        max_ver = max((_semver_key(i["version"]) for i in insts if i["version"]), default=(0,))
        max_time = max((i.get("collected_at") or "" for i in insts), default="")
        return (count, m_pop, completeness, max_ver, max_time)

    sorted_groups = sorted(groups.items(), key=_group_sort_key, reverse=True)
    config_groups = [
        {
            "signature": sig,
            "machines": [f"{i['host']}/{i['runtime_key']}" for i in insts],
            "count": len(insts)
        }
        for sig, insts in sorted_groups
    ]

    versions = sorted(
        {i["version"] for i in detected if i["version"]},
        key=lambda v: _semver_key(v),
    )
    return {
        "agent": agent_id,
        "name": _get_adapter(agent_id).display_name,
        "release_url": currency.get("release_url"),
        "installations": installations,
        "detected_count": len(detected),
        "config_groups": config_groups,
        "consistent_configs": len(config_groups) <= 1,
        "version": {
            **currency,
            "distinct_versions": versions,
            "latest_installed": versions[-1] if versions else None,
            "up_to_date": sum(
                1 for i in detected if i["version_state"] == "latest"
            ),
            "behind": sum(1 for i in detected if i["version_state"] == "behind"),
            # False only when no feed exists at all; npm and github both count
            # as real sources, so a github-backed agent is not "unknown".
            "unknown_source": currency.get("source") not in ("npm", "github"),
        },
    }


def _semver_key(value: str):
    core = ""
    for ch in value:
        if ch.isdigit() or ch == ".":
            core += ch
        else:
            break
    try:
        return tuple(int(p) for p in core.split(".") if p)
    except ValueError:
        return (0,)


@router.get("/compare/{agent_id}")
def compare_candidates(agent_id: str):
    """Installations available as comparison targets for one agent."""
    with session_scope() as s:
        rows = (
            s.execute(
                select(models.AgentInstallation)
                .where(models.AgentInstallation.agent_id == agent_id)
                .where(models.AgentInstallation.detected.is_(True))
            )
            .scalars()
            .all()
        )
        out = []
        for i in rows:
            rt = s.get(models.Runtime, i.runtime_id)
            out.append(
                {
                    "installation_id": i.id,
                    "agent": i.agent_id,
                    "status": i.status,
                    "version": i.version,
                    "runtime_id": i.runtime_id,
                    "host": rt.host.name if rt and rt.host else None,
                    "runtime_key": rt.key if rt else None,
                    "kind": rt.kind if rt else None,
                    "user": rt.collect_user if rt else None,
                    "model": i.config.model if i.config else None,
                    "config_status": i.config_status,
                    "collected_at": _iso(i.config.collected_at) if i.config else None,
                }
            )
        out.sort(key=lambda x: (x["host"] or "", x["runtime_key"] or ""))
        return {"agent": agent_id, "candidates": out}


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


@router.get("/export")
def export_fleet(
    runtime_id: str = Query(None),
    agent_id: str = Query(None),
    detected_only: bool = Query(False),
    include_disabled_hosts: bool = Query(False),
):
    """The whole fleet as one sanitized JSON document."""
    hidden = None
    if not include_disabled_hosts:
        with session_scope() as s:
            hidden = [
                r.id
                for r in s.execute(select(models.Runtime)).scalars()
                if r.host is None or not r.host.enabled
            ]
    return build_export(
        runtime_id=runtime_id,
        agent_id=agent_id,
        detected_only=detected_only,
        exclude_runtime_ids=hidden,
    )


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
                select(models.Runtime)
                .join(models.Host)
                .where(models.Runtime.enabled.is_(True))
                .where(models.Host.enabled.is_(True))
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


@router.get("/schedule")
def get_schedule_state():
    return get_schedule().status()


@router.put("/schedule")
async def update_schedule(payload: dict):
    enabled = payload.get("enabled")
    interval = payload.get("interval_seconds")
    return get_schedule().update(enabled=enabled, interval_seconds=interval)


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
