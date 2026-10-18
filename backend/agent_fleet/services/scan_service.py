"""Background scan runs: bounded concurrency, per-runtime timeouts."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services import persist
from agent_fleet.services.scanner import RuntimeRef, scan_runtime, to_ref

# Hard ceiling on simultaneous SSH connections across all runs.
_GLOBAL_LIMIT = 8
_global_sem: asyncio.Semaphore = None
_registry: dict = {}


def _sem() -> asyncio.Semaphore:
    global _global_sem
    if _global_sem is None:
        _global_sem = asyncio.Semaphore(_GLOBAL_LIMIT)
    return _global_sem


def active_runs() -> list:
    return [r for r in _registry.values() if r["status"] == "running"]


def is_scanning() -> bool:
    return any(r["status"] == "running" for r in _registry.values())


def run_status(run_id: str):
    return _registry.get(run_id)


def _touch(run_id, **kw):
    entry = _registry.get(run_id)
    if entry:
        entry.update(kw)


async def start_scan(runtime_ids, triggered_by, settings) -> str:
    run_id = models.new_id()
    with session_scope() as s:
        run = models.ScanRun(
            id=run_id,
            status="running",
            triggered_by=triggered_by,
            target_runtime_ids=list(runtime_ids),
            total=len(runtime_ids),
            concurrency=settings.scan_concurrency,
            started_at=datetime.utcnow(),
        )
        s.add(run)
        for rid in runtime_ids:
            s.add(
                models.ScanTask(
                    id=models.new_id(),
                    run_id=run_id,
                    runtime_id=rid,
                    status="pending",
                )
            )
        s.commit()
    _registry[run_id] = {
        "run_id": run_id,
        "status": "running",
        "total": len(runtime_ids),
        "done": 0,
        "ok": 0,
        "failed": 0,
        "started_at": time.time(),
        "concurrency": settings.scan_concurrency,
        "queue": len(runtime_ids),
    }
    asyncio.create_task(_execute(run_id, list(runtime_ids), settings))
    return run_id


async def _execute(run_id, runtime_ids, settings):
    queue: asyncio.Queue = asyncio.Queue()
    for rid in runtime_ids:
        await queue.put(rid)
    workers = max(1, min(settings.scan_concurrency, _GLOBAL_LIMIT))
    tasks = [asyncio.create_task(_worker(run_id, queue, settings)) for _ in range(workers)]
    await queue.join()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _finish(run_id)


async def _worker(run_id, queue, settings):
    while True:
        runtime_id = await queue.get()
        try:
            async with _sem():
                await _scan_one(run_id, runtime_id, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _mark_task(run_id, runtime_id, "failed", f"internal error: {exc}"[:400])
        finally:
            entry = _registry.get(run_id)
            if entry:
                entry["queue"] = max(0, entry["queue"] - 1)
            queue.task_done()


async def _scan_one(run_id, runtime_id, settings):
    ref = _load_ref(runtime_id)
    if ref is None:
        _mark_task(run_id, runtime_id, "failed", "runtime no longer exists")
        return
    if not ref.enabled:
        _mark_task(run_id, runtime_id, "skipped", "runtime disabled")
        return

    result = await scan_runtime(ref, settings)
    summary = persist.persist_result(runtime_id, result, run_id, settings)

    if result.ok:
        _mark_task(
            run_id,
            runtime_id,
            "ok",
            None,
            agents_found=summary["agents_found"],
            duration_ms=result.duration_ms,
        )
        _touch(run_id, ok=_registry[run_id]["ok"] + 1)
        if result.wsl_distros and getattr(settings, "discover_wsl", False):
            _ensure_wsl_runtimes(runtime_id, result.wsl_distros)
    else:
        _mark_task(
            run_id,
            runtime_id,
            "failed",
            result.error,
            duration_ms=result.duration_ms,
        )
        _touch(run_id, failed=_registry[run_id]["failed"] + 1)
    entry = _registry.get(run_id)
    if entry:
        entry["done"] = entry["done"] + 1


def _load_ref(runtime_id):
    """Read a runtime row into a plain ref, safe to hold across awaits."""
    with session_scope() as s:
        rt = s.get(models.Runtime, runtime_id)
        if rt is None:
            return None
        return to_ref(rt)


def _ensure_wsl_runtimes(parent_runtime_id, distros):
    """Create missing WSL runtimes and prune rows whose distro is gone.

    Pruning is deliberately narrow: only rows that never produced a successful
    collection are removed, so a temporarily unreachable distro keeps its data.
    """
    live_keys = {f"wsl:{d}" for d in distros}
    with session_scope() as s:
        parent = s.get(models.Runtime, parent_runtime_id)
        if parent is None:
            return
        rows = s.execute(
            select(models.Runtime).where(models.Runtime.host_id == parent.host_id)
        ).scalars().all()
        for d in distros:
            key = f"wsl:{d}"
            existing = next((r for r in rows if r.key == key), None)
            if existing is None:
                s.add(
                    models.Runtime(
                        id=models.new_id(),
                        host_id=parent.host_id,
                        key=key,
                        kind="wsl",
                        label=f"WSL {d}",
                        transport="wsl",
                        wsl_distro=d,
                        ssh_alias=parent.ssh_alias,
                        ssh_host=parent.ssh_host,
                        ssh_port=parent.ssh_port,
                        collect_user=parent.collect_user,
                        enabled=True,
                    )
                )
            elif existing.collect_user is None:
                existing.collect_user = parent.collect_user
        for r in rows:
            if not r.key.startswith("wsl:") or r.key in live_keys:
                continue
            # The distro is gone from this host.
            if r.last_success_at is None:
                # nothing was ever collected from it: drop the stale row
                for inst in list(r.installations):
                    s.delete(inst)
                s.delete(r)
                continue
            # it has history: keep the data, stop scanning it, say why
            r.enabled = False
            r.last_error = (
                "WSL distro is no longer present on this host "
                "(last successful collection kept for reference)"
            )
        s.commit()


def _mark_task(run_id, runtime_id, status, error, agents_found=0, duration_ms=0):
    with session_scope() as s:
        task = s.execute(
            select(models.ScanTask).where(
                models.ScanTask.run_id == run_id, models.ScanTask.runtime_id == runtime_id
            )
        ).scalar_one_or_none()
        if task is None:
            return
        task.status = status
        task.error = error
        task.agents_found = agents_found
        task.duration_ms = duration_ms
        task.finished_at = datetime.utcnow()
        if task.started_at is None:
            task.started_at = task.finished_at
        s.commit()


def _finish(run_id):
    with session_scope() as s:
        run = s.get(models.ScanRun, run_id)
        if run is None:
            return
        tasks = s.execute(
            select(models.ScanTask).where(models.ScanTask.run_id == run_id)
        ).scalars().all()
        ok = sum(1 for t in tasks if t.status == "ok")
        failed = sum(1 for t in tasks if t.status == "failed")
        skipped = sum(1 for t in tasks if t.status == "skipped")
        run.succeeded = ok
        run.failed = failed
        run.total = len(tasks)
        run.finished_at = datetime.utcnow()
        if failed and ok:
            run.status = "partial"
        elif failed and not ok:
            run.status = "failed"
        else:
            run.status = "completed"
        s.commit()
    entry = _registry.get(run_id)
    if entry:
        entry["status"] = "done"
        entry["skipped"] = skipped
        entry["finished_at"] = time.time()
