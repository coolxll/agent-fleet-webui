"""Read-only Herdr CLI observations through Fleet's existing runtime runners.

Verified against Herdr CLI: herdr status --json, herdr agent list, herdr workspace list.
No lifecycle commands, terminal keystrokes, secret variables or raw socket tokens are exposed/persisted.
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
from agent_fleet.herdr_models import HerdrObservation
from agent_fleet.remotes.probe import PATH_BOOTSTRAP
from agent_fleet.services.scanner import build_runner, to_ref
from agent_fleet.services.session_service import mask_sensitive_text

_ERRORS = {
    "not_installed": "Herdr CLI is not installed or not available on the runtime PATH.",
    "stopped": "Herdr server is stopped.",
    "not_ready": "Herdr server has not published a ready endpoint or socket.",
    "connection_failed": "Cannot connect to the runtime or Herdr server.",
    "authentication_failed": "Runtime or Herdr authentication is required or failed.",
    "unsupported": "This Herdr CLI returned an unsupported command or JSON schema.",
    "timeout": "Herdr observation timed out.",
    "internal_error": "Herdr observation failed.",
}

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
    if result.timed_out:
        return "timeout"
    raw = (result.stderr + " " + result.stdout + " " + (result.error or "")).lower()
    if any(x in raw for x in ("stopped", "server not running", "no server running", "not running")):
        return "stopped"
    if any(x in raw for x in ("auth_required", "auth_failed", "password required", "unauthorized", "permission denied")):
        return "authentication_failed"
    if any(x in raw for x in ("unknown option", "unknown command", "unsupported", "protocol mismatch")):
        return "unsupported"
    return "connection_failed"


async def _command(runner, windows, args, timeout):
    if windows:
        command = "& herdr " + " ".join("'" + a.replace("'", "''") + "'" for a in args)
        script = command + "\nexit $LASTEXITCODE"
    else:
        script = PATH_BOOTSTRAP + "\nexec herdr " + shlex.join(args) + "\n"
    return await runner.run_script(script, timeout)


def _sessions(payload, runtime_id):
    if not isinstance(payload, list):
        raise ValueError("unsupported agents list")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        agent_name = _text(item.get("agent"))
        pane_id = _text(item.get("pane_id")) or "unknown-pane"
        raw_status = _text(item.get("agent_status")) or "unknown"
        cwd = _text(item.get("cwd"), 2000)
        title = _text(item.get("terminal_title")) or _text(item.get("terminal_title_stripped")) or pane_id
        workspace_id = _text(item.get("workspace_id"))
        tab_id = _text(item.get("tab_id"))
        session_info = item.get("agent_session")
        provider_session_id = _text(session_info.get("value")) if isinstance(session_info, dict) else None
        rows.append({
            "source": "herdr",
            "runtime_id": runtime_id,
            "external_id": pane_id,
            "workspace_id": workspace_id,
            "tab_id": tab_id,
            "provider": agent_name,
            "provider_label": f"herdr/{agent_name}" if agent_name else "herdr",
            "provider_session_id": provider_session_id,
            "title": title,
            "cwd": cwd,
            "status": raw_status,
        })
    return rows


def _workspaces(payload):
    if not isinstance(payload, list):
        return []
    rows = []
    for ws in payload:
        if not isinstance(ws, dict):
            continue
        rows.append({
            "workspace_id": _text(ws.get("workspace_id")),
            "label": _text(ws.get("label")),
            "agent_status": _text(ws.get("agent_status")),
            "pane_count": ws.get("pane_count") if isinstance(ws.get("pane_count"), int) else 0,
            "tab_count": ws.get("tab_count") if isinstance(ws.get("tab_count"), int) else 0,
            "focused": bool(ws.get("focused")),
        })
    return rows


async def observe(ref, settings):
    runner = build_runner(ref, settings)
    windows = ref.transport == "windows"
    timeout = max(1, min(settings.command_timeout, 20))
    detect = (
        "if (Get-Command herdr -ErrorAction SilentlyContinue) { Write-Output 'FLEET_HERDR_FOUND' } else { Write-Output 'FLEET_HERDR_MISSING' }"
        if windows else PATH_BOOTSTRAP + "\nif command -v herdr >/dev/null 2>&1; then echo FLEET_HERDR_FOUND; else echo FLEET_HERDR_MISSING; fi\n"
    )
    result = await runner.run_script(detect, timeout)
    if not result.ok:
        return {"status": _failure(result)}
    if result.stdout.strip() == "FLEET_HERDR_MISSING":
        return {"status": "not_installed", "facts": {"installed": False}, "sessions_status": "unavailable"}
    if result.stdout.strip() != "FLEET_HERDR_FOUND":
        return {"status": "unsupported"}

    facts = {"installed": True}
    ver_res = await _command(runner, windows, ["--version"], timeout)
    if ver_res.ok:
        m = re.search(r"(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.\-]+)?)", ver_res.stdout)
        if m:
            facts["version"] = m.group(1)

    result = await _command(runner, windows, ["status", "--json"], timeout)
    if not result.ok:
        return {"status": _failure(result), "detected_facts": facts}

    payload = _json(result.stdout)
    if not isinstance(payload, dict):
        return {"status": "unsupported", "detected_facts": facts}

    client = payload.get("client")
    if isinstance(client, dict):
        if client.get("version"):
            facts["client_version"] = _text(client.get("version"))
        if client.get("channel"):
            facts["channel"] = _text(client.get("channel"))

    server = payload.get("server")
    if not isinstance(server, dict):
        return {"status": "unsupported", "detected_facts": facts}

    server_status = server.get("status")
    is_running = server.get("running") is True or server_status == "running"
    if not is_running:
        if server_status == "not_ready":
            return {"status": "not_ready", "facts": facts, "sessions_status": "unavailable"}
        return {"status": "stopped", "facts": facts, "sessions_status": "unavailable"}

    if server.get("version"):
        facts["server_version"] = _text(server.get("version"))
    if server.get("socket"):
        facts["socket"] = _text(server.get("socket"))
    if server.get("protocol") is not None:
        facts["protocol"] = str(server.get("protocol"))

    update = payload.get("update")
    if isinstance(update, dict):
        facts["restart_needed"] = bool(update.get("restart_needed"))

    # Workspaces inspection
    workspaces = []
    ws_res = await _command(runner, windows, ["workspace", "list"], timeout)
    if ws_res.ok:
        ws_payload = _json(ws_res.stdout)
        if isinstance(ws_payload, dict) and isinstance(ws_payload.get("result"), dict):
            workspaces = _workspaces(ws_payload["result"].get("workspaces", []))

    # Agents / sessions inspection
    result = await _command(runner, windows, ["agent", "list"], timeout)
    if not result.ok:
        return {"status": "running", "facts": facts, "workspaces": workspaces, "sessions_status": _failure(result), "sessions": []}

    try:
        ag_payload = _json(result.stdout)
        if isinstance(ag_payload, dict) and isinstance(ag_payload.get("result"), dict):
            sessions = _sessions(ag_payload["result"].get("agents", []), ref.id)
            sessions_status = "ok"
        else:
            sessions = []
            sessions_status = "unsupported"
    except ValueError:
        return {"status": "running", "facts": facts, "workspaces": workspaces, "sessions_status": "unsupported", "sessions": []}

    return {"status": "running", "facts": facts, "workspaces": workspaces, "sessions_status": sessions_status, "sessions": sessions}


def _persist(runtime_id, result):
    now = datetime.utcnow()
    with session_scope() as s:
        if s.get(models.Runtime, runtime_id) is None:
            return
        row = s.get(HerdrObservation, runtime_id)
        if row is None:
            row = HerdrObservation(runtime_id=runtime_id)
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
        if "workspaces" in result:
            row.workspaces = result["workspaces"]
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
                    pass

    await asyncio.gather(*(one(ref) for ref in refs))
    return list_observations(runtime_ids=runtime_ids)


def list_observations(runtime_ids=None):
    now = datetime.utcnow()
    try:
        from agent_fleet.services.updates import compare_versions, latest_available
        latest_info = latest_available("herdr")
    except Exception:
        latest_info = {}
    with session_scope() as s:
        query = select(models.Runtime).join(models.Host).order_by(models.Host.name, models.Runtime.key)
        if runtime_ids is not None:
            query = query.where(models.Runtime.id.in_(runtime_ids))
        items = []
        for rt in s.scalars(query).all():
            row = s.get(HerdrObservation, rt.id)
            facts = row.facts if row else {}
            current_ver = facts.get("server_version") or facts.get("version")
            latest_ver = latest_info.get("latest_available")
            upgrade_avail = bool(current_ver and latest_ver and compare_versions(current_ver, latest_ver) == -1)
            age = (now - row.last_success_at).total_seconds() if row and row.last_success_at else None
            stale = not row or row.checked_at != row.last_success_at or age is None or age > 300
            session_age = (now - row.sessions_success_at).total_seconds() if row and row.sessions_success_at else None
            items.append({
                "runtime_id": rt.id, "host_id": rt.host_id, "host": rt.host.name,
                "runtime_key": rt.key, "enabled": rt.enabled and rt.host.enabled,
                "status": row.status if row else "unknown", "installed": facts.get("installed"),
                "version": current_ver,
                "cli_version": facts.get("client_version") or facts.get("version"),
                "latest_version": latest_ver,
                "upgrade_available": upgrade_avail,
                "package": latest_info.get("package"),
                "release_url": latest_info.get("release_url"),
                "checked_at": models.iso_utc(row.checked_at) if row else None,
                "last_success_at": models.iso_utc(row.last_success_at) if row else None,
                "stale": stale, "error_code": row.error_code if row else None,
                "error": row.error if row else None,
                "connection": {
                    "transport": rt.transport,
                    "socket": facts.get("socket"),
                    "protocol": facts.get("protocol"),
                    "channel": facts.get("channel"),
                },
                "workspaces": row.workspaces if row else [],
                "sessions": row.sessions if row else [],
                "sessions_status": row.sessions_status if row else "unknown",
                "sessions_stale": not row or row.sessions_status != "ok" or session_age is None or session_age > 300,
                "sessions_checked_at": models.iso_utc(row.sessions_checked_at) if row else None,
                "sessions_success_at": models.iso_utc(row.sessions_success_at) if row else None,
            })
        return {"items": items}
