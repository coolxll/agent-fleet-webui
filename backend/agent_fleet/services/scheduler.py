"""Scheduled scanning.

A single background loop lives inside the API process. State is persisted so a
restart resumes the same schedule. Only one run is ever in flight: if a
scheduled run is due while a manual run is active it is skipped, never queued.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services import scan_service
from agent_fleet.settings import get_settings

log = logging.getLogger("agent_fleet.scheduler")

DEFAULTS = {"enabled": False, "interval_seconds": 3600}


class Schedule:
    def __init__(self):
        self._task: asyncio.Task = None
        self._wakeup = asyncio.Event()
        self._last_run_id: str = None
        self._last_run_at: datetime = None
        self._skipped = 0
        self._load()

    # ---- persistence ---------------------------------------------------
    def _load(self):
        with session_scope() as s:
            rows = s.execute(select(models.AppState)).scalars().all()
            stored = {r.key: r.value for r in rows}
        self.enabled = bool(stored.get("enabled", DEFAULTS["enabled"]))
        interval = stored.get("interval_seconds", DEFAULTS["interval_seconds"])
        try:
            self.interval_seconds = max(60, int(interval))
        except (TypeError, ValueError):
            self.interval_seconds = DEFAULTS["interval_seconds"]

    def save(self):
        with session_scope() as s:
            for key, value in (
                ("enabled", self.enabled),
                ("interval_seconds", self.interval_seconds),
            ):
                row = s.get(models.AppState, key)
                if row is None:
                    row = models.AppState(key=key, value=value)
                    s.add(row)
                else:
                    row.value = value
                    row.updated_at = datetime.utcnow()
            s.commit()

    # ---- state ---------------------------------------------------------
    def status(self) -> dict:
        next_at = None
        if self.enabled and self._last_run_at:
            # next run counts from the last actual run, not from "now".
            # _last_run_at is naive UTC, so add the delta instead of going
            # through .timestamp() (which would read it as local time).
            next_at = self._last_run_at + timedelta(seconds=self.interval_seconds)
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "running": bool(scan_service.active_runs()),
            "last_run_id": self._last_run_id,
            "last_run_at": (
                self._last_run_at.isoformat() + "Z" if self._last_run_at else None
            ),
            "skipped_because_busy": self._skipped,
            "next_run_at": next_at.isoformat() + "Z" if next_at else None,
        }

    def update(self, enabled=None, interval_seconds=None) -> dict:
        if enabled is not None:
            self.enabled = bool(enabled)
        if interval_seconds is not None:
            try:
                self.interval_seconds = max(60, int(interval_seconds))
            except (TypeError, ValueError):
                pass
        self.save()
        if self.enabled:
            self.start()
        return self.status()

    # ---- loop ----------------------------------------------------------
    def start(self):
        if self._task and not self._task.done():
            return
        self._wakeup.clear()
        try:
            self._task = asyncio.create_task(self._loop())
        except RuntimeError:
            # no running loop (sync context): the app lifespan will start it
            self._task = None

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self):
        while True:
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self.interval_seconds)
                self._wakeup.clear()
            except asyncio.TimeoutError:
                pass
            if not self.enabled:
                continue
            try:
                await self._run_once()
            except Exception as exc:  # never let the loop die
                log.warning("scheduled scan failed: %s", exc)

    async def _run_once(self):
        if not self.enabled:
            return
        if scan_service.active_runs():
            self._skipped += 1
            return
        with session_scope() as s:
            ids = [
                r.id
                for r in s.execute(
                    select(models.Runtime).where(models.Runtime.enabled.is_(True))
                ).scalars()
            ]
        if not ids:
            return
        # Count the run from the moment it starts: the next run waits a full
        # interval after this one began, not after it finished.
        run_id = await scan_service.start_scan(ids, "schedule", get_settings())
        self._last_run_id = run_id
        self._last_run_at = datetime.utcnow()
        log.info("scheduled scan %s started (%d runtimes)", run_id, len(ids))


_schedule = None


def get_schedule() -> Schedule:
    global _schedule
    if _schedule is None:
        _schedule = Schedule()
    return _schedule
