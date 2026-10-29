"""Command line entry point.

  python -m agent_fleet.cli sync
  python -m agent_fleet.cli runtimes
  python -m agent_fleet.cli probe <runtime-id-or-host>
  python -m agent_fleet.cli scan [--runtime ID] [--all]
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
from agent_fleet.db import create_all, init_engine, session_scope
from agent_fleet.services import persist
from agent_fleet.services.backup_service import create_backup
from agent_fleet.services.restore_service import (
    RestoreError,
    check_target_offline,
    restore_database,
)
from agent_fleet.services.inventory_sync import add_local_runtime, sync_inventory
from agent_fleet.services.scanner import scan_runtime, to_ref
from agent_fleet.settings import get_settings


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
