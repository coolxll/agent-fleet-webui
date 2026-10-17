"""Command line entry point.

  python -m agent_fleet.cli sync
  python -m agent_fleet.cli runtimes
  python -m agent_fleet.cli probe <runtime-id-or-host>
  python -m agent_fleet.cli scan [--runtime ID] [--all]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import create_all, init_engine, session_scope
from agent_fleet.services import persist
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

    args = p.parse_args(argv)
    settings = _setup()
    args.fn(settings, args)


if __name__ == "__main__":
    main()
