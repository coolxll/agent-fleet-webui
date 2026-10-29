"""SQLite-backed Host & Runner Fleet Inventory Service.

Unified single source of truth for hosts, SSH credentials, proxy egress,
and GitHub Actions Runner Fleet configurations.
"""

from __future__ import annotations

import logging
import time
import subprocess
from pathlib import Path
from typing import Any
import yaml

from sqlalchemy import select, delete
from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.remotes.ssh import build_ssh_command, resolve_ssh_target
from agent_fleet.settings import get_settings

logger = logging.getLogger(__name__)


def host_to_dict(h: models.Host, include_runtimes: bool = True) -> dict[str, Any]:
    """Serialize a Host model into a clean dictionary."""
    data = {
        "id": h.id,
        "name": h.name,
        "hostname": h.hostname or h.ssh_host or h.name,
        "description": h.description or "",
        "inventory_source": h.inventory_source,
        "group": h.group or "",
        "tags": h.tags or {},
        "ssh_host": h.ssh_host or h.hostname,
        "ssh_port": h.ssh_port or 22,
        "ssh_user": h.ssh_user or "",
        "proxy": h.proxy or "",
        "runner_enabled": bool(h.runner_enabled),
        "runner_max_concurrency": h.runner_max_concurrency or 2,
        "runner_labels": h.runner_labels or [h.name],
        "target_repos": h.target_repos or [],
        "enabled": bool(h.enabled),
        "created_at": models.iso_utc(h.created_at),
        "updated_at": models.iso_utc(h.updated_at),
    }
    if include_runtimes:
        data["runtimes"] = [
            {
                "id": r.id,
                "key": r.key,
                "kind": r.kind,
                "transport": r.transport,
                "enabled": bool(r.enabled),
                "reachable": r.reachable,
                "last_seen_at": models.iso_utc(r.last_seen_at),
                "last_error": r.last_error,
                "ssh_host": r.ssh_host,
                "ssh_port": r.ssh_port,
                "collect_user": r.collect_user,
            }
            for r in (h.runtimes or [])
        ]
        data["runtimes_count"] = len(data["runtimes"])
    return data


def seed_from_legacy_files_if_needed(force: bool = False) -> dict[str, int]:
    """Migrate legacy inventory.yaml and runner_fleet.yaml into SQLite seamlessly."""
    settings = get_settings()
    if not force and (not settings.inventory_paths or not getattr(settings, "auto_seed_legacy", True)):
        return {"created": 0, "updated": 0}

    created = 0
    updated = 0

    legacy_runner_fleet = Path("config/runner_fleet.yaml")
    fleet_hosts_cfg = {}
    fleet_pools_cfg = {}
    if legacy_runner_fleet.is_file():
        try:
            raw = yaml.safe_load(legacy_runner_fleet.read_text(encoding="utf-8")) or {}
            fleet_hosts_cfg = raw.get("hosts", {}) or {}
            fleet_pools_cfg = raw.get("pools", {}) or {}
        except Exception as exc:
            logger.warning("Failed to parse config/runner_fleet.yaml: %s", exc)

    # Invert pools mapping: host -> list of repos
    host_to_repos: dict[str, list[str]] = {}
    for repo, host_list in fleet_pools_cfg.items():
        if isinstance(host_list, list):
            for h in host_list:
                host_to_repos.setdefault(str(h), []).append(str(repo))

    with session_scope() as s:
        # Check if hosts already exist in SQLite
        existing_hosts = {h.name: h for h in s.execute(select(models.Host)).scalars().all()}
        if not force and len(existing_hosts) > 0:
            return {"created": 0, "updated": 0}

        # 1. Merge runner_fleet.yaml hosts
        for host_name, h_cfg in fleet_hosts_cfg.items():
            h_cfg = h_cfg or {}
            row = existing_hosts.get(host_name)
            labels = h_cfg.get("labels") or ["self-hosted", "Linux", "X64", host_name]
            repos = host_to_repos.get(host_name, [])
            proxy = h_cfg.get("proxy", "")
            user = h_cfg.get("user") or None
            max_r = h_cfg.get("max_runners", 4)
            desc = h_cfg.get("description", "")

            if row is None:
                row = models.Host(
                    id=models.new_id(),
                    name=host_name,
                    hostname=host_name,
                    description=desc,
                    inventory_source="sqlite",
                    ssh_host=host_name,
                    ssh_user=user,
                    proxy=proxy,
                    runner_enabled=True,
                    runner_max_concurrency=max_r,
                    runner_labels=labels,
                    target_repos=repos,
                    enabled=True,
                )
                s.add(row)
                s.flush()
                # Create default native runtime
                s.add(
                    models.Runtime(
                        id=models.new_id(),
                        host_id=row.id,
                        key="native",
                        kind="linux",
                        transport="ssh",
                        ssh_alias=host_name,
                        ssh_host=host_name,
                        collect_user=user,
                        enabled=True,
                    )
                )
                existing_hosts[host_name] = row
                created += 1
            else:
                row.proxy = proxy or row.proxy
                row.ssh_user = user or row.ssh_user
                row.runner_enabled = True
                row.runner_max_concurrency = max_r or row.runner_max_concurrency
                if labels:
                    row.runner_labels = labels
                if repos:
                    merged_repos = list(set((row.target_repos or []) + repos))
                    row.target_repos = merged_repos
                if desc and not row.description:
                    row.description = desc
                updated += 1

        # 2. Merge inventory.yaml hosts
        try:
            from agent_fleet.inventory import load_all
            inv_hosts = load_all(settings)
            for ih in inv_hosts:
                row = existing_hosts.get(ih.name)
                if row is None:
                    row = models.Host(
                        id=models.new_id(),
                        name=ih.name,
                        hostname=ih.hostname,
                        inventory_source="sqlite",
                        group=ih.group,
                        tags=ih.tags or {},
                        ssh_host=ih.hostname or ih.name,
                        ssh_port=ih.port or 22,
                        ssh_user=ih.user or None,
                        enabled=bool(ih.enabled),
                    )
                    s.add(row)
                    s.flush()
                    s.add(
                        models.Runtime(
                            id=models.new_id(),
                            host_id=row.id,
                            key="native",
                            kind=ih.kind_hint if ih.kind_hint != "auto" else "unknown",
                            transport="ssh" if ih.name != settings.local_host_label else "local",
                            ssh_alias=ih.name,
                            ssh_host=ih.hostname,
                            ssh_port=ih.port,
                            collect_user=ih.user,
                            enabled=bool(ih.enabled),
                        )
                    )
                    existing_hosts[ih.name] = row
                    created += 1
                else:
                    if ih.hostname and not row.hostname:
                        row.hostname = ih.hostname
                    if ih.port and not row.ssh_port:
                        row.ssh_port = ih.port
                    if ih.user and not row.ssh_user:
                        row.ssh_user = ih.user
                    if ih.group and not row.group:
                        row.group = ih.group
                    updated += 1
        except Exception as exc:
            logger.warning("Could not merge inventory.yaml: %s", exc)

    return {"created": created, "updated": updated}


def list_hosts() -> list[dict[str, Any]]:
    """Return all hosts from SQLite ordered by name."""
    with session_scope() as s:
        hosts = s.execute(select(models.Host).order_by(models.Host.name)).scalars().all()
        return [host_to_dict(h) for h in hosts]


def get_host(id_or_name: str) -> dict[str, Any] | None:
    """Get a single host by id or name."""
    with session_scope() as s:
        h = s.execute(
            select(models.Host).where((models.Host.id == id_or_name) | (models.Host.name == id_or_name))
        ).scalar_one_or_none()
        return host_to_dict(h) if h else None


def create_host(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a new host in SQLite."""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Host name 不能为空")

    with session_scope() as s:
        existing = s.execute(select(models.Host).where(models.Host.name == name)).scalar_one_or_none()
        if existing:
            raise ValueError(f"主机已存在: {name}")

        raw_user = payload.get("ssh_user")
        user = str(raw_user).strip() if raw_user is not None and str(raw_user).strip() else None
        h = models.Host(
            id=models.new_id(),
            name=name,
            hostname=payload.get("hostname") or name,
            description=payload.get("description", ""),
            inventory_source="sqlite",
            group=payload.get("group", ""),
            tags=payload.get("tags") or {},
            ssh_host=payload.get("ssh_host") or payload.get("hostname") or name,
            ssh_port=int(payload.get("ssh_port") or 22),
            ssh_user=user,
            proxy=payload.get("proxy", ""),
            runner_enabled=bool(payload.get("runner_enabled", False)),
            runner_max_concurrency=int(payload.get("runner_max_concurrency") or 2),
            runner_labels=payload.get("runner_labels") or ["self-hosted", "Linux", "X64", name],
            target_repos=payload.get("target_repos") or [],
            enabled=bool(payload.get("enabled", True)),
        )
        s.add(h)
        s.flush()

        # Add native primary runtime
        s.add(
            models.Runtime(
                id=models.new_id(),
                host_id=h.id,
                key="native",
                kind=payload.get("kind", "linux"),
                transport="ssh",
                ssh_alias=name,
                ssh_host=h.ssh_host,
                ssh_port=h.ssh_port,
                collect_user=user,
                enabled=h.enabled,
            )
        )
        s.flush()
        return host_to_dict(h)


def update_host(id_or_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Update an existing host in SQLite."""
    with session_scope() as s:
        h = s.execute(
            select(models.Host).where((models.Host.id == id_or_name) | (models.Host.name == id_or_name))
        ).scalar_one_or_none()
        if not h:
            raise ValueError(f"未找到主机: {id_or_name}")

        if "hostname" in payload:
            h.hostname = payload["hostname"]
        if "description" in payload:
            h.description = payload["description"]
        if "group" in payload:
            h.group = payload["group"]
        if "tags" in payload:
            h.tags = payload["tags"]
        if "ssh_host" in payload:
            h.ssh_host = payload["ssh_host"]
        if "ssh_port" in payload:
            h.ssh_port = int(payload["ssh_port"] or 22)
        if "ssh_user" in payload:
            h.ssh_user = payload["ssh_user"]
        if "proxy" in payload:
            h.proxy = payload["proxy"]
        if "runner_enabled" in payload:
            h.runner_enabled = bool(payload["runner_enabled"])
        if "runner_max_concurrency" in payload:
            h.runner_max_concurrency = int(payload["runner_max_concurrency"] or 2)
        if "runner_labels" in payload:
            h.runner_labels = payload["runner_labels"]
        if "target_repos" in payload:
            h.target_repos = payload["target_repos"]
        if "enabled" in payload:
            h.enabled = bool(payload["enabled"])
            # Sync enabled to runtimes
            for rt in h.runtimes:
                rt.enabled = h.enabled

        # Synchronize SSH connection parameters to native / primary runtimes
        if any(k in payload for k in ("ssh_host", "ssh_port", "ssh_user")):
            for rt in (h.runtimes or []):
                if rt.key in ("native", "primary"):
                    if "ssh_host" in payload:
                        rt.ssh_host = h.ssh_host
                    if "ssh_port" in payload:
                        rt.ssh_port = h.ssh_port
                    if "ssh_user" in payload:
                        rt.collect_user = h.ssh_user

        h.updated_at = models.utcnow()
        s.flush()
        return host_to_dict(h)


def delete_host(id_or_name: str) -> bool:
    """Delete a host and cascade delete its runtimes/data."""
    with session_scope() as s:
        h = s.execute(
            select(models.Host).where((models.Host.id == id_or_name) | (models.Host.name == id_or_name))
        ).scalar_one_or_none()
        if not h:
            return False
        s.delete(h)
        return True


def export_as_yaml() -> str:
    """Export SQLite hosts into clean YAML format."""
    hosts = list_hosts()
    doc = {
        "hosts": [
            {
                "name": h["name"],
                "hostname": h["hostname"],
                "user": h["ssh_user"],
                "port": h["ssh_port"],
                "group": h["group"] or None,
                "proxy": h["proxy"] or None,
                "runner": {
                    "enabled": h["runner_enabled"],
                    "max_concurrency": h["runner_max_concurrency"],
                    "labels": h["runner_labels"],
                    "target_repos": h["target_repos"],
                },
                "enabled": h["enabled"],
            }
            for h in hosts
        ]
    }
    return yaml.dump(doc, allow_unicode=True, sort_keys=False)


def test_host_connectivity(id_or_name: str) -> dict[str, Any]:
    """Test SSH connectivity for a given host."""
    host_data = get_host(id_or_name)
    if not host_data:
        return {"ok": False, "error": f"主机不存在: {id_or_name}"}

    ssh_host = host_data.get("ssh_host") or host_data.get("hostname") or host_data.get("name")
    ssh_port = host_data.get("ssh_port")
    ssh_user = host_data.get("ssh_user")

    target = resolve_ssh_target(
        ssh_host=host_data.get("ssh_host"),
        ssh_alias=host_data.get("hostname"),
        host_name=host_data.get("name"),
        ssh_user=ssh_user,
    )
    cmd = build_ssh_command(
        target=ssh_host,
        port=ssh_port,
        user=ssh_user,
        connect_timeout=5,
        remote_command="uname -srm",
    )
    start_time = time.time()
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=8,
        )
        elapsed_ms = int((time.time() - start_time) * 1000)
        if res.returncode == 0:
            return {
                "ok": True,
                "host": target,
                "latency_ms": elapsed_ms,
                "uname": res.stdout.strip(),
            }
        else:
            return {
                "ok": False,
                "host": target,
                "latency_ms": elapsed_ms,
                "error": res.stderr.strip() or f"SSH exited with code {res.returncode}",
            }
    except Exception as exc:
        return {
            "ok": False,
            "host": target,
            "latency_ms": int((time.time() - start_time) * 1000),
            "error": str(exc),
        }


test_ssh_connectivity = test_host_connectivity
