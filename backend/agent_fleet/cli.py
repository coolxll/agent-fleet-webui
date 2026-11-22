"""Command line entry point.

  python -m agent_fleet.cli sync
  python -m agent_fleet.cli runtimes
  python -m agent_fleet.cli probe <runtime-id-or-host>
  python -m agent_fleet.cli scan [--runtime ID] [--all]
  python -m agent_fleet.cli matrix [--agent NAME] [--host NAME]
  python -m agent_fleet.cli compare <host_a> <host_b> --agent NAME
  python -m agent_fleet.cli quotas [--agent NAME]
  python -m agent_fleet.cli tasks list [--status STATUS]
  python -m agent_fleet.cli tasks create --title TITLE [--description TEXT] [--target-host HOST] [--agent NAME]
  python -m agent_fleet.cli daemons [--host NAME]
  python -m agent_fleet.cli backup [--output-dir DIR] [--filename NAME] [--overwrite]
  python -m agent_fleet.cli restore <backup-path> [--target-db PATH] [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from sqlalchemy import select

from agent_fleet import models
from agent_fleet.adapters import sanitize
from agent_fleet.adapters.registry import agent_ids
from agent_fleet.db import create_all, init_engine, session_scope
from agent_fleet.herdr_models import HerdrObservation
from agent_fleet.paseo_models import PaseoObservation
from agent_fleet.services import diff, magpie_quota, persist, quota_store, task_service
from agent_fleet.services.backup_service import create_backup
from agent_fleet.services.restore_service import (
    RestoreError,
    check_target_offline,
    restore_database,
)
from agent_fleet.services.inventory_sync import add_local_runtime, sync_inventory
from agent_fleet.services.scanner import scan_runtime, to_ref
from agent_fleet.settings import get_settings
from agent_fleet.task_models import FleetTask, TaskStage


def _setup():
    settings = get_settings()
    init_engine(settings.db_path)
    create_all()
    return settings


def cmd_sync(settings, args):
    out = sync_inventory(settings)
    if settings.include_local_runtime:
        add_local_runtime(settings)
    print(json.dumps(out, indent=2))


def cmd_runtimes(settings, args):
    with session_scope() as s:
        rows = s.execute(select(models.Runtime)).scalars().all()
        for r in rows:
            print(
                f"{r.id}  {r.host.name}/{r.key:<16} kind={r.kind:<8} "
                f"transport={r.transport:<6} target={r.ssh_alias or r.ssh_host or '-'}"
                f" enabled={r.enabled}"
            )


def _resolve_runtime(args):
    with session_scope() as s:
        rows = s.execute(select(models.Runtime)).scalars().all()
        for r in rows:
            if r.id == args.target:
                return to_ref(r)
        for r in rows:
            if r.host and r.host.name == args.target and r.key == (args.key or "native"):
                return to_ref(r)
    return None


async def _probe(ref, settings):
    return await scan_runtime(ref, settings)


def cmd_probe(settings, args):
    ref = _resolve_runtime(args)
    if ref is None:
        print("runtime not found", file=sys.stderr)
        raise SystemExit(2)
    result = asyncio.run(_probe(ref, settings))
    print(json.dumps(_result_json(result), indent=2, default=str))


def _result_json(result):
    return {
        "runtime_id": result.runtime_id,
        "ok": result.ok,
        "error": result.error,
        "kind": result.kind,
        "os": result.os_name,
        "arch": result.arch,
        "user": result.user,
        "duration_ms": result.duration_ms,
        "wsl_distros": result.wsl_distros,
        "agents": [
            {
                "agent": a.agent_id,
                "status": a.detection.status,
                "detected": a.detection.detected,
                "version": a.detection.version,
                "exec_path": a.detection.exec_path,
                "install_method": a.detection.install_method,
                "config_status": a.config.parse_status,
                "model": a.config.model,
                "provider": a.config.model_provider,
                "base_url": a.config.base_url,
                "mcp": a.config.mcp_servers,
                "skills": a.config.skills,
                "warnings": a.config.warnings,
                "sources": [
                    {"path": s.path, "status": s.status, "size": s.size} for s in a.sources
                ],
            }
            for a in result.agents
        ],
    }


async def _scan_all(settings, refs):
    sem = asyncio.Semaphore(settings.scan_concurrency)

    async def one(ref):
        async with sem:
            res = await scan_runtime(ref, settings)
            persist.persist_result(ref.id, res, None, settings)
            return ref, res

    return await asyncio.gather(*[one(r) for r in refs])


def cmd_scan(settings, args):
    with session_scope() as s:
        rows = s.execute(select(models.Runtime)).scalars().all()
        refs = [to_ref(r) for r in rows if r.enabled]
        if args.runtime:
            refs = [r for r in refs if r.id == args.runtime]
        if args.host:
            refs = [r for r in refs if r.host_name == args.host]
    if not refs:
        print("no matching runtimes", file=sys.stderr)
        raise SystemExit(2)
    results = asyncio.run(_scan_all(settings, refs))
    for ref, res in results:
        print(f"--- {ref.host_name}/{ref.key} ok={res.ok} {res.duration_ms}ms {res.error or ''}")
        print(json.dumps(_result_json(res), indent=2, default=str))


def cmd_backup(settings, args):
    try:
        meta = create_backup(
            src_path=settings.db_path,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            filename=args.filename,
            overwrite=args.overwrite,
            settings=settings,
        )
        print(json.dumps(meta, indent=2))
    except (FileExistsError, FileNotFoundError, PermissionError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_restore(settings, args):
    def _progress(msg: str):
        print(f"[*] {msg}", file=sys.stderr)

    target_path = Path(args.target_db).resolve() if args.target_db else settings.db_path.resolve()
    force = args.force

    if target_path.exists():
        is_offline, reason = check_target_offline(target_path, settings=settings)
        if not is_offline and not force:
            print(f"Warning: Target database appears to be active: {reason}", file=sys.stderr)
            if sys.stdin.isatty():
                try:
                    ans = (
                        input("Target database may be active. Force restore and replace anyway? [y/N]: ")
                        .strip()
                        .lower()
                    )
                    if ans in ("y", "yes"):
                        force = True
                    else:
                        print("Restore aborted by user.", file=sys.stderr)
                        raise SystemExit(1)
                except (EOFError, KeyboardInterrupt):
                    print("\nRestore aborted.", file=sys.stderr)
                    raise SystemExit(1)
            else:
                print(
                    "Error: Target database is active. Pass --force to override in non-interactive mode.",
                    file=sys.stderr,
                )
                raise SystemExit(1)

    try:
        result = restore_database(
            backup_path=args.backup_path,
            target_path=target_path,
            force=force,
            settings=settings,
            progress_callback=_progress,
        )
        print(json.dumps(result, indent=2))
    except (RestoreError, FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)


def _print_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _matches_host(rt, host_filter: str | None) -> bool:
    if not host_filter:
        return True
    host_name = rt.host.name if rt.host else None
    if rt.id == host_filter:
        return True
    if host_name == host_filter:
        return True
    if host_name and f"{host_name}/{rt.key}" == host_filter:
        return True
    return False


def _resolve_runtime_target(session, target: str):
    rows = session.execute(select(models.Runtime)).scalars().all()
    for r in rows:
        if r.id == target:
            return r
    if "/" in target:
        host_name, key = target.split("/", 1)
        for r in rows:
            if r.host and r.host.name == host_name and r.key == key:
                return r
    host_matches = [r for r in rows if r.host and r.host.name == target]
    if not host_matches:
        return None
    for r in host_matches:
        if r.key == "native":
            return r
    host_matches.sort(key=lambda r: r.key or "")
    return host_matches[0]


def _is_stale(last_success_at, ttl_seconds: int) -> bool | None:
    if not last_success_at:
        return None
    age = (models.utcnow() - last_success_at).total_seconds()
    return age > (ttl_seconds + 60)


def _matrix_cell_compact(inst, rt) -> dict:
    if rt.last_scan_at is None:
        return {
            "status": "not_scanned",
            "installed": False,
            "version": None,
            "config_status": "not_scanned",
            "provider": None,
            "model": None,
            "base_url": None,
            "base_url_masked": False,
            "warnings": [],
        }
    if rt.reachable is False and rt.last_error:
        return {
            "status": "scan_failed",
            "installed": bool(inst and inst.detected),
            "version": inst.version if inst and inst.detected else None,
            "config_status": inst.config_status if inst else "not_scanned",
            "provider": None,
            "model": None,
            "base_url": None,
            "base_url_masked": False,
            "warnings": [],
            "error": sanitize.redact_value(rt.last_error),
        }
    if inst is None:
        return {
            "status": "unknown",
            "installed": False,
            "version": None,
            "config_status": "not_scanned",
            "provider": None,
            "model": None,
            "base_url": None,
            "base_url_masked": False,
            "warnings": [],
        }
    cfg = getattr(inst, "config", None)
    masked_url, url_changed = (
        sanitize.mask_url(cfg.base_url) if cfg and cfg.base_url else (None, False)
    )
    base_url_masked = bool((cfg.base_url_masked if cfg else False) or url_changed)
    warnings = sanitize.sanitize_object(cfg.warnings or []) if cfg else []
    cell = {
        "status": inst.status,
        "installed": bool(inst.detected and inst.status == "installed"),
        "version": inst.version,
        "config_status": inst.config_status,
        "provider": sanitize.redact_value(cfg.model_provider) if cfg else None,
        "model": sanitize.redact_value(cfg.model) if cfg else None,
        "base_url": masked_url,
        "base_url_masked": base_url_masked,
        "warnings": warnings if isinstance(warnings, list) else [],
    }
    if inst.error:
        cell["error"] = sanitize.redact_value(inst.error)
    return cell


def cmd_matrix(settings, args):
    known_agents = agent_ids()
    if args.agent and args.agent not in known_agents:
        print(f"unknown agent: {args.agent}", file=sys.stderr)
        raise SystemExit(2)
    selected_agents = [args.agent] if args.agent else known_agents

    with session_scope() as s:
        q = select(models.Runtime).join(models.Host)
        if not args.include_disabled:
            q = q.where(models.Host.enabled.is_(True))
        rts = s.execute(q).scalars().all()
        rts = [r for r in rts if _matches_host(r, args.host)]
        rts.sort(key=lambda r: ((r.host.name if r.host else ""), r.key or ""))

        ids = [r.id for r in rts]
        rows = (
            s.execute(
                select(models.AgentInstallation).where(
                    models.AgentInstallation.runtime_id.in_(ids)
                )
            )
            .scalars()
            .all()
            if ids
            else []
        )
        by_rt: dict[str, dict[str, models.AgentInstallation]] = {}
        for inst in rows:
            by_rt.setdefault(inst.runtime_id, {})[inst.agent_id] = inst

        installed_count = 0
        absent_count = 0
        error_count = 0
        runtime_items = []
        for rt in rts:
            cells = {}
            for aid in selected_agents:
                cell = _matrix_cell_compact(by_rt.get(rt.id, {}).get(aid), rt)
                cells[aid] = cell
                if cell["installed"]:
                    installed_count += 1
                elif cell["status"] in ("error", "scan_failed"):
                    error_count += 1
                else:
                    absent_count += 1
            runtime_items.append(
                {
                    "runtime_id": rt.id,
                    "host": rt.host.name if rt.host else None,
                    "runtime_key": rt.key,
                    "kind": rt.kind,
                    "reachable": rt.reachable,
                    "last_success_at": models.iso_utc(rt.last_success_at),
                    "stale": _is_stale(rt.last_success_at, settings.scan_ttl_seconds),
                    "agents": cells,
                }
            )

    _print_json(
        {
            "filters": {"agent": args.agent, "host": args.host},
            "runtimes": runtime_items,
            "summary": {
                "runtimes": len(runtime_items),
                "installed": installed_count,
                "absent": absent_count,
                "errors": error_count,
            },
        }
    )


def _sanitize_change_value(path: str, value):
    if value is None:
        return None
    if path == "base_url" or path.endswith(".base_url"):
        masked, _ = sanitize.mask_url(value)
        return masked
    return sanitize.redact_value(value)


def cmd_compare(settings, args):
    with session_scope() as s:
        rt_a = _resolve_runtime_target(s, args.host_a)
        if rt_a is None:
            print(f"runtime not found: {args.host_a}", file=sys.stderr)
            raise SystemExit(2)
        rt_b = _resolve_runtime_target(s, args.host_b)
        if rt_b is None:
            print(f"runtime not found: {args.host_b}", file=sys.stderr)
            raise SystemExit(2)

        ia = s.execute(
            select(models.AgentInstallation).where(
                models.AgentInstallation.runtime_id == rt_a.id,
                models.AgentInstallation.agent_id == args.agent,
            )
        ).scalar_one_or_none()
        if ia is None:
            print(
                f"agent '{args.agent}' not found on {rt_a.runtime_identity}",
                file=sys.stderr,
            )
            raise SystemExit(2)

        ib = s.execute(
            select(models.AgentInstallation).where(
                models.AgentInstallation.runtime_id == rt_b.id,
                models.AgentInstallation.agent_id == args.agent,
            )
        ).scalar_one_or_none()
        if ib is None:
            print(
                f"agent '{args.agent}' not found on {rt_b.runtime_identity}",
                file=sys.stderr,
            )
            raise SystemExit(2)

        result = diff.compare_installations(ia, ib)
        for side, rt in (("a", rt_a), ("b", rt_b)):
            result[side]["runtime"] = {
                "host": rt.host.name if rt.host else None,
                "key": rt.key,
                "kind": rt.kind,
                "collect_user": rt.collect_user,
            }
        sanitized_changes = []
        for ch in result.get("changes") or []:
            path = ch.get("path", "")
            if sanitize.is_secret_key(path.split(".")[-1]):
                continue
            sanitized_changes.append(
                {
                    "path": path,
                    "kind": ch.get("kind"),
                    "a": _sanitize_change_value(path, ch.get("a")),
                    "b": _sanitize_change_value(path, ch.get("b")),
                }
            )
        result["changes"] = sanitized_changes
        result["identical"] = not sanitized_changes

    _print_json(result)


def _compact_quota_accounts(quota: dict | None) -> list[dict]:
    if not isinstance(quota, dict):
        return []
    raw_accounts = quota.get("accounts")
    if not isinstance(raw_accounts, list):
        return []
    out = []
    for acc in raw_accounts:
        if not isinstance(acc, dict):
            continue
        out.append(
            {
                "label": sanitize.redact_value(acc.get("label") or acc.get("name")),
                "email": sanitize.redact_value(acc.get("email")),
                "plan": sanitize.redact_value(acc.get("plan")),
                "min_remaining_fraction": acc.get("min_remaining_fraction"),
            }
        )
    return out


def cmd_quotas(settings, args):
    runtime_quotas = []
    with session_scope() as s:
        q = select(models.Runtime).join(models.Host)
        if not args.include_disabled:
            q = q.where(models.Host.enabled.is_(True))
        runtimes = s.execute(q).scalars().all()
        by_id = {r.id: r for r in runtimes}
        rows = (
            s.execute(
                select(models.AgentInstallation).where(
                    models.AgentInstallation.runtime_id.in_(list(by_id))
                )
            )
            .scalars()
            .all()
            if by_id
            else []
        )
        for inst in rows:
            if args.agent and inst.agent_id != args.agent:
                continue
            cfg = getattr(inst, "config", None)
            quota = getattr(cfg, "quota", None) if cfg is not None else None
            if not isinstance(quota, dict):
                continue
            accounts = quota.get("accounts") if isinstance(quota.get("accounts"), list) else []
            if not accounts:
                continue
            rt = by_id.get(inst.runtime_id)
            if rt is None:
                continue
            fractions = [
                a.get("min_remaining_fraction")
                for a in accounts
                if isinstance(a, dict)
                and isinstance(a.get("min_remaining_fraction"), (int, float))
            ]
            runtime_quotas.append(
                {
                    "agent": inst.agent_id,
                    "host": rt.host.name if rt.host else None,
                    "runtime_id": rt.id,
                    "runtime_key": rt.key,
                    "source": sanitize.redact_value(quota.get("source")),
                    "observed_at": quota.get("observed_at"),
                    "collected_at": models.iso_utc(cfg.collected_at) if cfg else None,
                    "account_count": len(accounts),
                    "min_remaining_fraction": min(fractions) if fractions else None,
                    "accounts": _compact_quota_accounts(quota),
                }
            )
    runtime_quotas.sort(key=lambda x: ((x["host"] or ""), x["runtime_key"] or "", x["agent"]))

    imported_error = None
    imported_accounts = []
    try:
        raw_imported = quota_store.list_accounts()
    except ValueError as exc:
        raw_imported = []
        imported_error = sanitize.redact_value(str(exc))

    for acc in raw_imported:
        if args.agent and acc.get("agent") != args.agent and acc.get("provider") != args.agent:
            continue
        imported_accounts.append(
            {
                "id": acc.get("id"),
                "provider": acc.get("provider"),
                "agent": acc.get("agent"),
                "label": sanitize.redact_value(acc.get("label")),
                "email": sanitize.redact_value(acc.get("email")),
                "plan": sanitize.redact_value(acc.get("plan")),
                "status": acc.get("status"),
                "credential_source": acc.get("credential_source"),
                "last_refreshed_at": acc.get("last_refreshed_at"),
                "last_checked_at": acc.get("last_checked_at"),
                "error_code": acc.get("error_code"),
                "last_error": sanitize.redact_value(acc.get("last_error")),
                "quota_summary": sanitize.sanitize_object(acc.get("quota_summary")),
            }
        )

    magpie_info = magpie_quota.source_accounts()
    sources = {
        "magpie": {
            "available": bool(magpie_info.get("available")),
            "error": sanitize.redact_value(magpie_info.get("error")),
            "account_count": len(magpie_info.get("accounts") or []),
            "accounts": [
                {
                    "id": a.get("id"),
                    "label": sanitize.redact_value(a.get("label")),
                    "email": sanitize.redact_value(a.get("email")),
                    "plan": sanitize.redact_value(a.get("plan")),
                    "linked_account_id": a.get("linked_account_id"),
                }
                for a in (magpie_info.get("accounts") or [])
            ],
        }
    }

    payload = {
        "filters": {"agent": args.agent},
        "runtime_quotas": runtime_quotas,
        "imported_accounts": imported_accounts,
        "sources": sources,
    }
    if imported_error:
        payload["imported_accounts_error"] = imported_error
    _print_json(payload)


def cmd_tasks_list(settings, args):
    with session_scope() as s:
        q = select(FleetTask).order_by(FleetTask.updated_at.desc())
        if args.status:
            q = q.where(FleetTask.status == args.status)
        tasks = s.execute(q).scalars().all()
        task_ids = [t.id for t in tasks]
        stages_by_task: dict[str, list[TaskStage]] = {}
        if task_ids:
            stages = s.execute(
                select(TaskStage)
                .where(TaskStage.task_id.in_(task_ids))
                .order_by(TaskStage.created_at, TaskStage.id)
            ).scalars().all()
            for st in stages:
                stages_by_task.setdefault(st.task_id, []).append(st)

        items = []
        for t in tasks:
            t_stages = stages_by_task.get(t.id, [])
            latest = t_stages[-1] if t_stages else None
            items.append(
                {
                    "id": t.id,
                    "title": t.title,
                    "goal": t.goal,
                    "status": t.status,
                    "project_path": t.project_path,
                    "target_host": latest.host_name if latest and latest.host_name else None,
                    "agent_id": latest.agent_id if latest else None,
                    "stage_count": len(t_stages),
                    "latest_stage_status": latest.status if latest else None,
                    "created_at": models.iso_utc(t.created_at),
                    "updated_at": models.iso_utc(t.updated_at),
                }
            )

    _print_json(
        {
            "status_filter": args.status,
            "count": len(items),
            "tasks": items,
        }
    )


def cmd_tasks_create(settings, args):
    title = (args.title or "").strip()
    if not title:
        print("Error: --title must not be empty", file=sys.stderr)
        raise SystemExit(2)

    goal_parts = []
    if args.description and args.description.strip():
        goal_parts.append(args.description.strip())
    meta_lines = []
    if args.target_host and args.target_host.strip():
        meta_lines.append(f"target_host: {args.target_host.strip()}")
    if args.agent and args.agent.strip():
        meta_lines.append(f"agent: {args.agent.strip()}")
    if meta_lines:
        goal_parts.append("\n".join(meta_lines))
    goal = "\n\n".join(goal_parts)

    created = task_service.create_task(
        {
            "title": title,
            "goal": goal,
            "status": args.status,
            "project_path": (args.project_path or "").strip(),
        }
    )
    _print_json(
        {
            "id": created["id"],
            "title": created["title"],
            "goal": created["goal"],
            "status": created["status"],
            "project_path": created["project_path"],
            "target_host": (args.target_host or "").strip() or None,
            "agent": (args.agent or "").strip() or None,
            "created_at": created["created_at"],
            "updated_at": created["updated_at"],
        }
    )


def cmd_daemons(settings, args):
    now = models.utcnow()
    with session_scope() as s:
        q = select(models.Runtime).join(models.Host).order_by(models.Host.name, models.Runtime.key)
        if not args.include_disabled:
            q = q.where(models.Host.enabled.is_(True))
        runtimes = [r for r in s.execute(q).scalars().all() if _matches_host(r, args.host)]

        herdr_items = []
        paseo_items = []
        for rt in runtimes:
            h_row = s.get(HerdrObservation, rt.id)
            h_facts = (h_row.facts if h_row and isinstance(h_row.facts, dict) else {})
            h_age = (now - h_row.last_success_at).total_seconds() if h_row and h_row.last_success_at else None
            h_stale = not h_row or h_row.checked_at != h_row.last_success_at or h_age is None or h_age > 300
            herdr_items.append(
                {
                    "runtime_id": rt.id,
                    "host": rt.host.name if rt.host else None,
                    "runtime_key": rt.key,
                    "enabled": bool(rt.enabled and rt.host and rt.host.enabled),
                    "status": h_row.status if h_row else "unknown",
                    "installed": h_facts.get("installed"),
                    "version": h_facts.get("server_version") or h_facts.get("version"),
                    "stale": h_stale,
                    "checked_at": models.iso_utc(h_row.checked_at) if h_row else None,
                    "last_success_at": models.iso_utc(h_row.last_success_at) if h_row else None,
                    "error_code": h_row.error_code if h_row else None,
                    "error": sanitize.redact_value(h_row.error) if h_row else None,
                    "workspace_count": len(h_row.workspaces) if h_row and isinstance(h_row.workspaces, list) else 0,
                    "session_count": len(h_row.sessions) if h_row and isinstance(h_row.sessions, list) else 0,
                    "sessions_status": h_row.sessions_status if h_row else "unknown",
                }
            )

            p_row = s.get(PaseoObservation, rt.id)
            p_facts = (p_row.facts if p_row and isinstance(p_row.facts, dict) else {})
            p_age = (now - p_row.last_success_at).total_seconds() if p_row and p_row.last_success_at else None
            p_stale = not p_row or p_row.checked_at != p_row.last_success_at or p_age is None or p_age > 300
            paseo_items.append(
                {
                    "runtime_id": rt.id,
                    "host": rt.host.name if rt.host else None,
                    "runtime_key": rt.key,
                    "enabled": bool(rt.enabled and rt.host and rt.host.enabled),
                    "status": p_row.status if p_row else "unknown",
                    "installed": p_facts.get("installed"),
                    "version": p_facts.get("daemonVersion") or p_facts.get("version"),
                    "stale": p_stale,
                    "checked_at": models.iso_utc(p_row.checked_at) if p_row else None,
                    "last_success_at": models.iso_utc(p_row.last_success_at) if p_row else None,
                    "error_code": p_row.error_code if p_row else None,
                    "error": sanitize.redact_value(p_row.error) if p_row else None,
                    "session_count": len(p_row.sessions) if p_row and isinstance(p_row.sessions, list) else 0,
                    "sessions_status": p_row.sessions_status if p_row else "unknown",
                }
            )

    _print_json(
        {
            "filters": {"host": args.host},
            "herdr": herdr_items,
            "paseo": paseo_items,
        }
    )


def main(argv=None):
    p = argparse.ArgumentParser(prog="agent-fleet")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    sub.add_parser("runtimes").set_defaults(fn=cmd_runtimes)

    pr = sub.add_parser("probe")
    pr.add_argument("target")
    pr.add_argument("--key", default="native")
    pr.set_defaults(fn=cmd_probe)

    sc = sub.add_parser("scan")
    sc.add_argument("--runtime")
    sc.add_argument("--host")
    sc.set_defaults(fn=cmd_scan)

    mx = sub.add_parser("matrix", help="Compact JSON summary of agents x machines")
    mx.add_argument("--agent", default=None, help="Filter to a single agent id")
    mx.add_argument("--host", default=None, help="Filter to a host name, host/key, or runtime id")
    mx.add_argument("--include-disabled", action="store_true", help="Include disabled hosts")
    mx.set_defaults(fn=cmd_matrix)

    cp = sub.add_parser("compare", help="Field-level diff of the same agent across two machines")
    cp.add_argument("host_a", help="First host name, host/key, or runtime id")
    cp.add_argument("host_b", help="Second host name, host/key, or runtime id")
    cp.add_argument("--agent", required=True, help="Agent id to compare")
    cp.set_defaults(fn=cmd_compare)

    qt = sub.add_parser("quotas", help="Sanitized JSON summary of quota sources and latest readings")
    qt.add_argument("--agent", default=None, help="Filter to a single agent/provider id")
    qt.add_argument("--include-disabled", action="store_true", help="Include disabled hosts")
    qt.set_defaults(fn=cmd_quotas)

    tk = sub.add_parser("tasks", help="Inspect and create task board items")
    tk_sub = tk.add_subparsers(dest="tasks_cmd", required=True)

    tk_list = tk_sub.add_parser("list", help="List task board items")
    tk_list.add_argument("--status", choices=task_service.STATUSES, default=None, help="Filter by status")
    tk_list.set_defaults(fn=cmd_tasks_list)

    tk_create = tk_sub.add_parser("create", help="Create a task board item")
    tk_create.add_argument("--title", required=True, help="Task title")
    tk_create.add_argument("--description", default="", help="Task description / goal")
    tk_create.add_argument("--target-host", default=None, help="Intended target host name")
    tk_create.add_argument("--agent", default=None, help="Intended agent id")
    tk_create.add_argument("--project-path", default="", help="Project working directory path")
    tk_create.add_argument("--status", choices=task_service.STATUSES, default="todo", help="Initial task status")
    tk_create.set_defaults(fn=cmd_tasks_create)

    dm = sub.add_parser("daemons", help="Cached JSON summary of Herdr and Paseo daemon observations")
    dm.add_argument("--host", default=None, help="Filter to a host name, host/key, or runtime id")
    dm.add_argument("--include-disabled", action="store_true", help="Include disabled hosts")
    dm.set_defaults(fn=cmd_daemons)

    bk = sub.add_parser("backup", help="Create consistent SQLite backup with integrity check")
    bk.add_argument("--output-dir", default=None, help="Output directory for backup (default: data/backups/)")
    bk.add_argument("--filename", default=None, help="Custom filename for the backup (default: fleet_backup_YYYYMMDD_HHMMSS.db)")
    bk.add_argument("--overwrite", action="store_true", help="Overwrite existing backup files")
    bk.set_defaults(fn=cmd_backup)

    rs = sub.add_parser("restore", help="Restore SQLite database from verified backup with rollback safety")
    rs.add_argument("backup_path", help="Path to backup SQLite database file (.db)")
    rs.add_argument("--target-db", default=None, help="Target SQLite database path (default: configured db_path)")
    rs.add_argument("--force", action="store_true", help="Bypass target database offline and active WAL checks")
    rs.set_defaults(fn=cmd_restore)

    args = p.parse_args(argv)
    if args.cmd in ("backup", "restore"):
        settings = get_settings()
    else:
        settings = _setup()
    args.fn(settings, args)


if __name__ == "__main__":
    main()
