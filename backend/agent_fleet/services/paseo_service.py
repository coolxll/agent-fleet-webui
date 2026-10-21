"""Read-only Paseo CLI observations through Fleet's existing runtime runners.

Verified against getpaseo/paseo 23b05bb (CLI 0.12.0): daemon status --json
returns an object and ls -a --json returns an array. No lifecycle commands,
config files, pairing APIs or raw CLI output are exposed/persisted.
"""
from __future__ import annotations

import asyncio
import json
import re
import shlex
from datetime import datetime

from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.paseo_models import PaseoObservation
from agent_fleet.remotes.probe import PATH_BOOTSTRAP
from agent_fleet.services.scanner import build_runner, to_ref
from agent_fleet.services.session_service import mask_sensitive_text

_ERRORS = {
    "not_installed": "Paseo CLI is not installed or not available on the runtime PATH.",
    "stopped": "Paseo daemon is stopped.",
    "not_ready": "Paseo daemon has not published a ready endpoint.",
    "connection_failed": "Cannot connect to the runtime or Paseo daemon.",
    "authentication_failed": "Runtime or Paseo authentication is required or failed.",
    "unsupported": "This Paseo CLI returned an unsupported command or JSON schema.",
    "timeout": "Paseo observation timed out.",
    "internal_error": "Paseo observation failed.",
}
# Keep refreshes bounded even if multiple clients submit requests.
_gate = asyncio.Semaphore(4)
_locks: dict[str, asyncio.Lock] = {}


def _text(value, limit=500):
    return mask_sensitive_text(value)[0][:limit] if isinstance(value, str) else None


def _json(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _failure(result):
    """Classify in memory; never echo exception messages or raw stdout/stderr."""
    if result.timed_out:
        return "timeout"
    raw = (result.stderr + " " + result.stdout + " " + (result.error or "")).lower()
    if any(x in raw for x in ("auth_required", "auth_failed", "password required", "unauthorized", "permission denied", "authentication")):
        return "authentication_failed"
    if any(x in raw for x in ("unknown option", "unknown command", "unsupported", "protocol", "schema")):
        return "unsupported"
    return "connection_failed"


async def _command(runner, windows, args, timeout):
    if windows:
        # Literal commands only; no UI input enters scripts.
        command = "& paseo " + " ".join("'" + a.replace("'", "''") + "'" for a in args)
        script = "Remove-Item Env:PASEO_HOST -ErrorAction SilentlyContinue\n" + command + "\nexit $LASTEXITCODE"
    else:
        # Observe this runtime's home, never an inherited remote PASEO_HOST.
        script = PATH_BOOTSTRAP + "\nunset PASEO_HOST\nexec paseo " + shlex.join(args) + "\n"
    return await runner.run_script(script, timeout)


def _sessions(payload, runtime_id):
    if not isinstance(payload, list) or any(not isinstance(x, dict) or not isinstance(x.get("id"), str) for x in payload):
        raise ValueError("unsupported sessions")
    rows = []
    for item in payload:
        label = _text(item.get("provider"))
        rows.append({
            "source": "paseo", "runtime_id": runtime_id,
            "external_id": _text(item["id"]),
            "provider": label.split("/", 1)[0] if label else None,
            "provider_label": label,
            "provider_session_id": None,  # CLI does not export native session IDs.
            "title": _text(item.get("name")), "cwd": _text(item.get("cwd"), 2000),
            "status": _text(item.get("status"), 100) or "unknown",
        })
    return rows


async def observe(ref, settings):
    runner = build_runner(ref, settings)
    windows = ref.transport == "windows"
    timeout = max(1, min(settings.command_timeout, 20))
    detect = (
        "if (Get-Command paseo -ErrorAction SilentlyContinue) { Write-Output 'FLEET_PASEO_FOUND' } else { Write-Output 'FLEET_PASEO_MISSING' }"
        if windows else PATH_BOOTSTRAP + "\nif command -v paseo >/dev/null 2>&1; then echo FLEET_PASEO_FOUND; else echo FLEET_PASEO_MISSING; fi\n"
    )
    result = await runner.run_script(detect, timeout)
    if not result.ok:
        return {"status": _failure(result)}
    if result.stdout.strip() == "FLEET_PASEO_MISSING":
        return {"status": "not_installed", "facts": {"installed": False}, "sessions_status": "unavailable"}
    if result.stdout.strip() != "FLEET_PASEO_FOUND":
        return {"status": "unsupported"}
    facts = {"installed": True}
    version = await _command(runner, windows, ["--version"], timeout)
    if version.ok and re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.\-]+)?", version.stdout.strip()):
        facts["version"] = version.stdout.strip().lstrip("v")
    result = await _command(runner, windows, ["daemon", "status", "--json"], timeout)
    if not result.ok:
        return {"status": _failure(result), "detected_facts": facts}
    payload = _json(result.stdout)
    if not isinstance(payload, dict):
        return {"status": "unsupported", "detected_facts": facts}
    local, connected = payload.get("localDaemon"), payload.get("connectedDaemon")
    if connected in ("auth_required", "auth_failed"):
        return {"status": "authentication_failed", "detected_facts": facts}
    if local == "stopped":
        return {"status": "stopped", "facts": facts, "sessions_status": "unavailable"}
    if local == "not_ready":
        return {"status": "not_ready", "facts": facts, "sessions_status": "unavailable"}
    if connected != "reachable":
        return {"status": "connection_failed" if connected == "unreachable" else "unsupported", "detected_facts": facts}
    # Never expose relay info, pairing links, config, logs or arbitrary endpoint URIs.
    for key in ("daemonVersion", "serverId"):
        if isinstance(payload.get(key), str):
            facts[key] = _text(payload[key])
    endpoint = payload.get("listen")
    if isinstance(endpoint, str) and re.fullmatch(r"[A-Za-z0-9.\[\]:_-]+", endpoint):
        facts["endpoint"] = endpoint[:200]
    result = await _command(runner, windows, ["ls", "-a", "--json"], timeout)
    if not result.ok:
        return {"status": "running", "facts": facts, "sessions_status": _failure(result)}
    try:
        sessions = _sessions(_json(result.stdout), ref.id)
    except ValueError:
        return {"status": "running", "facts": facts, "sessions_status": "unsupported"}
    return {"status": "running", "facts": facts, "sessions_status": "ok", "sessions": sessions}


def _persist(runtime_id, result):
    now = datetime.utcnow()
    with session_scope() as s:
        if s.get(models.Runtime, runtime_id) is None:
            return
        row = s.get(PaseoObservation, runtime_id)
        if row is None:
            row = PaseoObservation(runtime_id=runtime_id)
            s.add(row)
        row.checked_at = now
        row.status = result["status"]
        row.error_code = None if row.status == "running" else row.status
        row.error = _ERRORS.get(row.error_code)
        if "facts" in result:
            row.facts = result["facts"]
            row.last_success_at = now
        elif "detected_facts" in result:
            row.facts = {**(row.facts or {}), **result["detected_facts"]}
        row.sessions_checked_at = now
        row.sessions_status = result.get("sessions_status", "unavailable")
        if "sessions" in result:
            row.sessions = result["sessions"]
            row.sessions_success_at = now


async def refresh(runtime_ids, settings):
    with session_scope() as s:
        query = select(models.Runtime).join(models.Host).where(models.Runtime.enabled.is_(True), models.Host.enabled.is_(True))
        if runtime_ids is not None:
            query = query.where(models.Runtime.id.in_(runtime_ids))
        refs = [to_ref(rt) for rt in s.scalars(query).all()]

    async def one(ref):
        async with _locks.setdefault(ref.id, asyncio.Lock()):
            async with _gate:
                try:
                    result = await asyncio.wait_for(observe(ref, settings), timeout=85)
                except asyncio.TimeoutError:
                    result = {"status": "timeout"}
                except Exception:
                    result = {"status": "internal_error"}
                try:
                    _persist(ref.id, result)
                except Exception:
                    # A deleted runtime or independent write failure must not
                    # abort observations for the rest of the fleet.
                    pass
    await asyncio.gather(*(one(ref) for ref in refs))
    return list_observations(runtime_ids=runtime_ids)


def list_observations(runtime_ids=None):
    now = datetime.utcnow()
    try:
        from agent_fleet.services.updates import compare_versions, latest_available
        latest_info = latest_available("paseo")
    except Exception:
        latest_info = {}
    with session_scope() as s:
        query = select(models.Runtime).join(models.Host).order_by(models.Host.name, models.Runtime.key)
        if runtime_ids is not None:
            query = query.where(models.Runtime.id.in_(runtime_ids))
        items = []
        for rt in s.scalars(query).all():
            row = s.get(PaseoObservation, rt.id)
            facts = row.facts if row else {}
            current_ver = facts.get("daemonVersion") or facts.get("version")
            latest_ver = latest_info.get("latest_available")
            upgrade_avail = bool(current_ver and latest_ver and compare_versions(current_ver, latest_ver) == -1)
            age = (now - row.last_success_at).total_seconds() if row and row.last_success_at else None
            stale = not row or row.checked_at != row.last_success_at or age is None or age > 300
            session_age = (now - row.sessions_success_at).total_seconds() if row and row.sessions_success_at else None
            items.append({
                "runtime_id": rt.id, "host_id": rt.host_id, "host": rt.host.name,
                "runtime_key": rt.key, "enabled": rt.enabled and rt.host.enabled,
                "status": row.status if row else "unknown", "installed": facts.get("installed"),
                "version": current_ver, "cli_version": facts.get("version"),
                "latest_version": latest_ver,
                "upgrade_available": upgrade_avail,
                "package": latest_info.get("package"),
                "release_url": latest_info.get("release_url"),
                "checked_at": models.iso_utc(row.checked_at) if row else None,
                "last_success_at": models.iso_utc(row.last_success_at) if row else None,
                "stale": stale, "error_code": row.error_code if row else None,
                "error": row.error if row else None,
                "connection": {"transport": rt.transport, "endpoint": facts.get("endpoint"), "server_id": facts.get("serverId")},
                "sessions": row.sessions if row else [],
                "sessions_status": row.sessions_status if row else "unknown",
                "sessions_stale": not row or row.sessions_status != "ok" or session_age is None or session_age > 300,
                "sessions_checked_at": models.iso_utc(row.sessions_checked_at) if row else None,
                "sessions_success_at": models.iso_utc(row.sessions_success_at) if row else None,
            })
        return {"items": items}
