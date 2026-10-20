"""Inventory sync: discovered hosts become Host + Runtime rows."""

from __future__ import annotations

from sqlalchemy import delete, select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.inventory import load_all


def _kind_hint(name: str, hint: str) -> str:
    if hint and hint != "auto":
        return hint
    low = name.lower()
    if low.endswith("-wsl"):
        return "wsl"
    if low.endswith("-win") or low.endswith("-windows"):
        return "windows"
    return "auto"


def _is_controller_host(name: str, tags: dict | None, group: str | None, settings) -> bool:
    if name == settings.local_host_label:
        return True
    if isinstance(tags, dict) and tags.get("role") == "controller":
        return True
    if group == "controller":
        return True
    return False


def _find_controller_host_name(settings, hosts_list=None) -> str:
    """Find the host name that acts as the local controller."""
    if hosts_list is None:
        try:
            hosts_list = load_all(settings)
        except Exception:
            hosts_list = []
    # 1. Any inventory host explicitly tagged role=controller or group=controller
    for h in hosts_list:
        if (isinstance(h.tags, dict) and h.tags.get("role") == "controller") or h.group == "controller":
            return h.name
    # 2. Any inventory host matching local_host_label
    for h in hosts_list:
        if h.name == settings.local_host_label:
            return h.name
    # 3. Default fallback
    return settings.local_host_label


def _cleanup_phantom_local_hosts(s, active_controller_name: str) -> int:
    """Remove placeholder hosts created with inventory_source='local' that are superseded."""
    removed = 0
    phantoms = s.execute(
        select(models.Host).where(
            models.Host.inventory_source == "local",
            models.Host.name != active_controller_name,
        )
    ).scalars().all()
    for phantom in phantoms:
        for rt in phantom.runtimes:
            s.execute(
                delete(models.AgentConfiguration).where(
                    models.AgentConfiguration.installation_id.in_(
                        select(models.AgentInstallation.id).where(
                            models.AgentInstallation.runtime_id == rt.id
                        )
                    )
                )
            )
            s.execute(delete(models.Snapshot).where(models.Snapshot.runtime_id == rt.id))
            s.execute(delete(models.ScanTask).where(models.ScanTask.runtime_id == rt.id))
            s.delete(rt)
        s.delete(phantom)
        removed += 1
    return removed


def sync_inventory(settings) -> dict:
    """Upsert hosts/runtimes from all inventory sources. Non-destructive."""
    hosts = load_all(settings)
    controller_name = _find_controller_host_name(settings, hosts)
    created_hosts = created_runtimes = updated = 0
    with session_scope() as s:
        for h in hosts:
            row = s.execute(select(models.Host).where(models.Host.name == h.name)).scalar_one_or_none()
            if row is None:
                row = models.Host(
                    id=models.new_id(),
                    name=h.name,
                    hostname=h.hostname,
                    inventory_source=h.source,
                    group=h.group,
                    tags=h.tags or {},
                    enabled=True,
                )
                s.add(row)
                created_hosts += 1
            else:
                row.hostname = h.hostname or row.hostname
                row.group = h.group or row.group
                if h.tags:
                    merged = dict(row.tags or {})
                    merged.update(h.tags)
                    row.tags = merged
                updated += 1
            s.flush()

            kind = _kind_hint(h.name, h.kind_hint)
            is_controller = _is_controller_host(h.name, h.tags, h.group, settings) or (h.name == controller_name)

            primary = s.execute(
                select(models.Runtime).where(
                    models.Runtime.host_id == row.id, models.Runtime.key == "native"
                )
            ).scalar_one_or_none()
            if primary is None:
                if is_controller:
                    transport = "local"
                elif kind == "windows":
                    transport = "windows"
                else:
                    transport = "ssh"
                primary = models.Runtime(
                    id=models.new_id(),
                    host_id=row.id,
                    key="native",
                    kind=kind if kind != "auto" else "unknown",
                    transport=transport,
                    ssh_alias=h.name,
                    ssh_host=h.hostname,
                    ssh_port=h.port,
                    collect_user=h.user,
                    enabled=h.enabled,
                )
                s.add(primary)
                created_runtimes += 1
            else:
                if is_controller:
                    primary.transport = "local"
                primary.ssh_host = h.hostname or primary.ssh_host
                primary.ssh_port = h.port or primary.ssh_port
                primary.collect_user = h.user or primary.collect_user
                # The transport hint decides how we reach the machine, so it is
                # always applied. The kind hint is only a pre-scan guess: once a
                # probe has observed the runtime, the observation wins.
                if kind == "windows":
                    primary.transport = "windows"
                if kind != "auto" and primary.last_scan_at is None:
                    primary.kind = kind

            # declared WSL runtimes (project yaml only)
            for w in h.wsl or []:
                key = f"wsl:{w.distro}"
                existing = s.execute(
                    select(models.Runtime).where(
                        models.Runtime.host_id == row.id, models.Runtime.key == key
                    )
                ).scalar_one_or_none()
                if existing is None:
                    s.add(
                        models.Runtime(
                            id=models.new_id(),
                            host_id=row.id,
                            key=key,
                            kind="wsl",
                            transport="wsl",
                            wsl_distro=w.distro,
                            wsl_user=w.user or h.user,
                            ssh_alias=h.name,
                            enabled=h.enabled,
                        )
                    )
                    created_runtimes += 1
                else:
                    existing.wsl_user = w.user or existing.wsl_user

        # Clean up any phantom local placeholder rows superseded by inventory controller
        _cleanup_phantom_local_hosts(s, controller_name)
        s.commit()
    return {
        "hosts_seen": len(hosts),
        "hosts_created": created_hosts,
        "runtimes_created": created_runtimes,
        "updated": updated,
    }


def add_local_runtime(settings):
    """Ensure the controller machine has a runtime row."""
    import socket

    controller_name = _find_controller_host_name(settings)
    with session_scope() as s:
        row = s.execute(select(models.Host).where(models.Host.name == controller_name)).scalar_one_or_none()
        if row is None:
            row = models.Host(
                id=models.new_id(),
                name=controller_name,
                hostname=socket.gethostname(),
                inventory_source="local",
                group="controller",
            )
            s.add(row)
            s.flush()

        rt = s.execute(
            select(models.Runtime).where(
                models.Runtime.host_id == row.id, models.Runtime.key == "native"
            )
        ).scalar_one_or_none()
        if rt is None:
            s.add(
                models.Runtime(
                    id=models.new_id(),
                    host_id=row.id,
                    key="native",
                    kind="unknown",
                    transport="local",
                    collect_user=None,
                )
            )
        else:
            rt.transport = "local"

        _cleanup_phantom_local_hosts(s, row.name)
        s.commit()
    return row.name
