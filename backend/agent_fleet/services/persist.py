"""Persistence for scan results.

Keeps last-known-good data on failure: a failed scan updates connectivity
fields but never overwrites agent facts collected by an earlier success.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services.scanner import RuntimeScanResult


def _now() -> datetime:
    return datetime.utcnow()


def persist_result(runtime_id, result: RuntimeScanResult, run_id, settings) -> dict:
    agents_found = 0
    now = _now()
    with session_scope() as s:
        stored = s.get(models.Runtime, runtime_id)
        if stored is None:
            return {"agents_found": 0, "ok": False, "error": "runtime missing"}

        stored.last_scan_at = now
        if result.ok:
            stored.reachable = True
            stored.last_seen_at = now
            if result.kind != "unknown":
                stored.kind = result.kind
            stored.os_name = result.os_name
            stored.arch = result.arch
            if result.home:
                stored.home_dir = result.home
            stored.collect_user = result.user or stored.collect_user
            stored.last_error = None
        else:
            stored.reachable = False
            stored.last_error = result.error

        payload = {
            "kind": result.kind,
            "os": result.os_name,
            "arch": result.arch,
            "user": result.user,
            "agents": [],
        }

        for ar in result.agents:
            inst = s.execute(
                select(models.AgentInstallation).where(
                    models.AgentInstallation.runtime_id == stored.id,
                    models.AgentInstallation.agent_id == ar.agent_id,
                )
            ).scalar_one_or_none()
            if inst is None:
                inst = models.AgentInstallation(
                    id=models.new_id(), runtime_id=stored.id, agent_id=ar.agent_id
                )
                s.add(inst)
                s.flush()

            det = ar.detection
            status = det.status
            if result.ok and not det.detected:
                has_cfg = any(
                    src.status == "ok" for src in ar.sources if src.kind in ("config", "catalog")
                )
                status = "config_only" if has_cfg else "not_installed"
            if not result.ok:
                inst.error = result.error
            else:
                inst.status = status
                inst.detected = det.detected
                inst.detected_at = now
                inst.exec_path = det.exec_path
                inst.install_method = det.install_method
                inst.config_dir = det.config_dir
                inst.version = det.version
                inst.version_raw = det.version_raw
                inst.error = det.error

            if result.ok:
                # sources
                s.execute(
                    delete(models.ConfigSource).where(
                        models.ConfigSource.installation_id == inst.id
                    )
                )
                for src in ar.sources:
                    s.add(
                        models.ConfigSource(
                            id=models.new_id(),
                            installation_id=inst.id,
                            scope=src.scope,
                            path=src.path,
                            format=src.format,
                            kind=src.kind,
                            present=src.status in ("ok", "toolarge", "truncated", "isdir"),
                            readable=src.status == "ok",
                            size_bytes=src.size,
                            status=_source_status(src),
                            error=src.error,
                            captured_at=now,
                        )
                    )
                cfg = _store_config(s, inst, ar.config, now)
                inst.config_status = ar.config.parse_status
                inst.config_id = cfg.id if cfg else None
            if det.detected:
                agents_found += 1

            payload["agents"].append(
                {
                    "agent": ar.agent_id,
                    "status": status,
                    "detected": det.detected,
                    "version": det.version,
                    "exec_path": det.exec_path,
                    "install_method": det.install_method,
                    "config_status": ar.config.parse_status,
                    "config": ar.config.sanitized or {},
                }
            )

        if result.ok:
            stored.last_success_at = now
            snap = models.Snapshot(
                id=models.new_id(),
                runtime_id=stored.id,
                scan_run_id=run_id,
                created_at=now,
                payload=payload,
                agents_found=agents_found,
            )
            s.add(snap)
            s.flush()
            stored.last_snapshot_id = snap.id
        s.commit()
    return {"agents_found": agents_found, "ok": result.ok, "error": result.error}


def _source_status(src) -> str:
    return {
        "ok": "ok",
        "missing": "missing",
        "isdir": "unsupported",
        "toolarge": "unreadable",
        "truncated": "unreadable",
        "unreadable": "unreadable",
        "unknown": "unknown",
    }.get(src.status, "unknown")


def _store_config(s, inst, cfg, now):
    if cfg.parse_status in ("empty", "error", "unsupported") and not cfg.sanitized:
        # keep the previous config if this run produced nothing usable
        return None
    row = models.AgentConfiguration(
        id=models.new_id(),
        installation_id=inst.id,
        resolution=cfg.resolution,
        parse_status=cfg.parse_status,
        model=cfg.model,
        model_provider=cfg.model_provider,
        base_url=cfg.base_url,
        base_url_masked=cfg.base_url_masked,
        mcp_servers=cfg.mcp_servers,
        skills=cfg.skills,
        settings=cfg.settings,
        unsupported_fields=cfg.unsupported_fields,
        sanitized=cfg.sanitized,
        warnings=cfg.warnings,
        collected_at=now,
    )
    s.add(row)
    s.flush()
    # keep at most the current config per installation
    old = s.execute(
        select(models.AgentConfiguration)
        .where(models.AgentConfiguration.installation_id == inst.id)
        .where(models.AgentConfiguration.id != row.id)
    ).scalars().all()
    for o in old:
        s.delete(o)
    return row
