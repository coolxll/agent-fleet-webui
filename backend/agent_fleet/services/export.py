"""Export: the whole fleet as one sanitized JSON document.

Only whitelisted, sanitized data ever reaches this module - it is the same
payload the UI shows, not a new path to raw configuration.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    return str(value)


def build_export(runtime_id=None, agent_id=None, detected_only=False) -> dict:
    with session_scope() as s:
        rt_q = select(models.Runtime).order_by(models.Runtime.key)
        if runtime_id:
            rt_q = rt_q.where(models.Runtime.id == runtime_id)
        runtimes = s.execute(rt_q).scalars().all()

        hosts = []
        for rt in runtimes:
            if rt.host_id is None:
                continue
            host = s.get(models.Host, rt.host_id)
            if host is None:
                continue
            inst_q = (
                select(models.AgentInstallation)
                .where(models.AgentInstallation.runtime_id == rt.id)
                .order_by(models.AgentInstallation.agent_id)
            )
            if agent_id:
                inst_q = inst_q.where(models.AgentInstallation.agent_id == agent_id)
            if detected_only:
                inst_q = inst_q.where(models.AgentInstallation.detected.is_(True))
            installs = s.execute(inst_q).scalars().all()
            if not installs:
                continue
            hosts.append(
                {
                    "host": host.name,
                    "inventory_source": host.inventory_source,
                    "runtime": {
                        "id": rt.id,
                        "key": rt.key,
                        "kind": rt.kind,
                        "label": rt.label,
                        "transport": rt.transport,
                        "ssh_target": rt.ssh_alias or rt.ssh_host,
                        "collect_user": rt.collect_user,
                        "os_name": rt.os_name,
                        "arch": rt.arch,
                        "enabled": rt.enabled,
                        "reachable": rt.reachable,
                        "last_success_at": _iso(rt.last_success_at),
                        "last_scan_at": _iso(rt.last_scan_at),
                        "last_error": rt.last_error,
                    },
                    "agents": [
                        {
                            "agent": i.agent_id,
                            "status": i.status,
                            "detected": i.detected,
                            "version": i.version,
                            "exec_path": i.exec_path,
                            "install_method": i.install_method,
                            "config_dir": i.config_dir,
                            "config_status": i.config_status,
                            "error": i.error,
                            "config": _config_view(i),
                            "sources": [
                                {
                                    "scope": src.scope,
                                    "path": src.path,
                                    "format": src.format,
                                    "kind": src.kind,
                                    "present": src.present,
                                    "readable": src.readable,
                                    "size_bytes": src.size_bytes,
                                    "status": src.status,
                                }
                                for src in sorted(i.sources, key=lambda x: x.path)
                            ],
                        }
                        for i in installs
                    ],
                }
            )

    return {
        "schema": "agent-fleet-export/1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "filters": {
            "runtime_id": runtime_id,
            "agent_id": agent_id,
            "detected_only": detected_only,
        },
        "counts": {
            "runtimes": len(hosts),
            "installations": sum(len(h["agents"]) for h in hosts),
        },
        "hosts": hosts,
    }


def _config_view(inst):
    cfg = getattr(inst, "config", None)
    if cfg is None:
        return None
    return {
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
        "warnings": cfg.warnings,
        "collected_at": _iso(cfg.collected_at),
    }
