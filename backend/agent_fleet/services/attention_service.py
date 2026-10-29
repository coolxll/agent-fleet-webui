"""Attention aggregation service.

Aggregates attention-worthy anomalies across the fleet:
- scan_failed: Hosts/Runtimes where last scan failed (reachable is False or last_error is set).
- scan_stale: Enabled hosts where probe data is older than stale threshold or never scanned.
- task_blocked: Fleet tasks whose execution is blocked, interrupted, or failed.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.models import Host, Runtime, iso_utc, utcnow
from agent_fleet.task_models import FleetTask, TaskStage

DEFAULT_STALE_THRESHOLD_SECONDS = 24 * 3600  # 24 hours


def _collect_attention_items(
    db,
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    now = utcnow()
    stale_cutoff = now - timedelta(seconds=stale_threshold_seconds)

    # -------------------------------------------------------------------------
    # 1. Host Scan Failures & Stale / Never Scanned Hosts
    # -------------------------------------------------------------------------
    hosts = db.scalars(
        select(Host).where(Host.enabled.is_(True)).order_by(Host.name)
    ).all()

    for host in hosts:
        rts = host.runtimes or []

        # Check for explicit scan failures
        failed_rt = None
        for rt in rts:
            if not rt.enabled:
                continue
            is_explicit_failed = (
                getattr(rt, "last_scan_status", None) == "failed"
                or bool(rt.last_error)
                or (rt.reachable is False and rt.last_scan_at is not None)
            )
            if is_explicit_failed:
                failed_rt = rt
                break

        # Also check host-level override if present
        host_explicit_failed = getattr(host, "last_scan_status", None) == "failed"

        if failed_rt is not None or host_explicit_failed:
            err_msg = (
                (failed_rt.last_error if failed_rt else None)
                or getattr(host, "last_error", None)
                or f"Scan failed for host {host.name}"
            )
            item_id = f"host:{host.id}:scan_failed"
            if item_id not in seen_ids:
                seen_ids.add(item_id)
                last_time = (
                    failed_rt.last_scan_at
                    if (failed_rt and failed_rt.last_scan_at)
                    else host.updated_at
                )
                items.append(
                    {
                        "id": item_id,
                        "type": "scan_failed",
                        "severity": "error",
                        "target_type": "host",
                        "target_id": host.id,
                        "target_name": host.name,
                        "message": f"Scan failed on {host.name}: {err_msg}",
                        "first_detected_at": iso_utc(last_time),
                        "last_updated_at": iso_utc(last_time),
                        "action_view": "machines",
                        "action_payload": {
                            "host_id": host.id,
                            "runtime_id": failed_rt.id if failed_rt else None,
                            "error": err_msg,
                        },
                    }
                )
            # Once flagged as scan_failed, do not duplicate as scan_stale
            continue

        # Check for never scanned vs stale
        probe_times = [
            r.last_success_at or r.last_scan_at
            for r in rts
            if (r.last_success_at or r.last_scan_at)
        ]
        host_probed_at = getattr(host, "last_probed_at", None)
        if host_probed_at:
            probe_times.append(host_probed_at)

        if not probe_times:
            # Never scanned
            item_id = f"host:{host.id}:scan_stale"
            if item_id not in seen_ids:
                seen_ids.add(item_id)
                items.append(
                    {
                        "id": item_id,
                        "type": "scan_stale",
                        "severity": "warning",
                        "target_type": "host",
                        "target_id": host.id,
                        "target_name": host.name,
                        "message": f"Host '{host.name}' has never been scanned",
                        "first_detected_at": iso_utc(host.created_at),
                        "last_updated_at": iso_utc(host.updated_at),
                        "action_view": "machines",
                        "action_payload": {
                            "host_id": host.id,
                            "subtype": "never_scanned",
                        },
                    }
                )
        else:
            latest_probed = max(probe_times)
            if latest_probed < stale_cutoff:
                item_id = f"host:{host.id}:scan_stale"
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    age_seconds = int((now - latest_probed).total_seconds())
                    items.append(
                        {
                            "id": item_id,
                            "type": "scan_stale",
                            "severity": "warning",
                            "target_type": "host",
                            "target_id": host.id,
                            "target_name": host.name,
                            "message": f"Host '{host.name}' scan data is stale (last scanned {iso_utc(latest_probed)})",
                            "first_detected_at": iso_utc(latest_probed),
                            "last_updated_at": iso_utc(latest_probed),
                            "action_view": "machines",
                            "action_payload": {
                                "host_id": host.id,
                                "subtype": "stale",
                                "last_probed_at": iso_utc(latest_probed),
                                "age_seconds": age_seconds,
                            },
                        }
                    )

    # -------------------------------------------------------------------------
    # 2. Blocked / Interrupted / Failed Tasks
    # -------------------------------------------------------------------------
    tasks = db.scalars(
        select(FleetTask).where(FleetTask.status != "done").order_by(FleetTask.updated_at.desc())
    ).all()

    for task in tasks:
        # Load stages for this task
        stages = db.scalars(
            select(TaskStage)
            .where(TaskStage.task_id == task.id)
            .order_by(TaskStage.created_at.asc())
        ).all()

        latest_stage = stages[-1] if stages else None

        # Determine execution state
        execution_state = None
        if hasattr(task, "execution_state") and getattr(task, "execution_state"):
            execution_state = getattr(task, "execution_state")
        elif latest_stage and latest_stage.status in ("blocked", "interrupted", "failed"):
            execution_state = latest_stage.status
        elif task.status in ("blocked", "interrupted", "failed"):
            execution_state = task.status

        if execution_state in ("blocked", "interrupted", "failed"):
            item_id = f"task:{task.id}:task_blocked"
            if item_id not in seen_ids:
                seen_ids.add(item_id)
                severity = "error" if execution_state == "failed" else "warning"
                err_text = latest_stage.error if (latest_stage and latest_stage.error) else None
                if err_text:
                    msg = f"Task '{task.title or task.id[:8]}' {execution_state}: {err_text}"
                else:
                    msg = f"Task '{task.title or task.id[:8]}' execution is {execution_state}"

                first_time = (
                    latest_stage.created_at
                    if (latest_stage and latest_stage.created_at)
                    else task.created_at
                )
                last_time = (
                    latest_stage.updated_at
                    if (latest_stage and latest_stage.updated_at)
                    else task.updated_at
                )

                items.append(
                    {
                        "id": item_id,
                        "type": "task_blocked",
                        "severity": severity,
                        "target_type": "task",
                        "target_id": task.id,
                        "target_name": task.title or f"Task {task.id[:8]}",
                        "message": msg,
                        "first_detected_at": iso_utc(first_time),
                        "last_updated_at": iso_utc(last_time),
                        "action_view": "tasks",
                        "action_payload": {
                            "task_id": task.id,
                            "stage_id": latest_stage.id if latest_stage else None,
                            "execution_state": execution_state,
                            "error": err_text,
                        },
                    }
                )

    # Sort items: error first, then warning; within same severity, newest last_updated_at first
    def _sort_key(item: dict) -> tuple[int, str]:
        sev_rank = 0 if item["severity"] == "error" else 1
        updated = item.get("last_updated_at") or ""
        return (sev_rank, f"-{updated}")

    items.sort(key=lambda it: (0 if it["severity"] == "error" else 1, it.get("last_updated_at") or ""), reverse=False)
    # Actually sort so newest last_updated_at comes first within severity
    items.sort(
        key=lambda it: (
            0 if it["severity"] == "error" else 1,
            -(datetime.fromisoformat(it["last_updated_at"].rstrip("Z")).timestamp())
            if it.get("last_updated_at")
            else 0,
        )
    )

    return items


def get_attention_summary(
    db=None,
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
    limit: int = 50,
) -> dict[str, Any]:
    """Return aggregated attention items and anomaly counts across the fleet."""
    if db is not None:
        all_items = _collect_attention_items(db, stale_threshold_seconds)
    else:
        with session_scope() as session:
            all_items = _collect_attention_items(session, stale_threshold_seconds)

    err_count = sum(1 for it in all_items if it["severity"] == "error")
    warn_count = sum(1 for it in all_items if it["severity"] == "warning")

    return {
        "total": len(all_items),
        "items": all_items[:limit] if limit is not None else all_items,
        "counts": {
            "error": err_count,
            "warning": warn_count,
        },
    }
