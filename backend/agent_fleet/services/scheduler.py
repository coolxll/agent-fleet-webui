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
from agent_fleet.models import iso_utc
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
        # Wake time currently armed in the loop, so status() and update() agree
        # with what the sleeping loop will actually do.
        self._arm_deadline: datetime = None
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
        # Report the time the loop is actually sleeping until. Deriving it from
        # the last run and the current interval would advertise a time the
        # sleeping loop never armed, e.g. right after an interval change.
        next_at = self._arm_deadline if self.enabled else None
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "running": scan_service.is_scanning(),
            "last_run_id": self._last_run_id,
            "last_run_at": iso_utc(self._last_run_at),
            "skipped_because_busy": self._skipped,
            "next_run_at": iso_utc(next_at),
        }

    def update(self, enabled=None, interval_seconds=None) -> dict:
        was_enabled = self.enabled
        old_interval = self.interval_seconds
        if enabled is not None:
            self.enabled = bool(enabled)
        if interval_seconds is not None:
            try:
                self.interval_seconds = max(60, int(interval_seconds))
            except (TypeError, ValueError):
                pass
        self.save()
        if self.enabled:
            if not was_enabled:
                # Flipping the switch on starts a run now, like a manual scan.
                self._arm(datetime.utcnow())
            elif self.interval_seconds != old_interval:
                # Next run still counts from the last run, but an interval that
                # is already past fires immediately instead of waiting another
                # full cycle.
                base = self._last_run_at or datetime.utcnow()
                self._arm(
                    max(datetime.utcnow(), base + timedelta(seconds=self.interval_seconds))
                )
            # The loop keeps the timeout it is sleeping with until woken. If it
            # has not started yet, it picks the deadline set above.
            self.start()
            self._wakeup.set()
        return self.status()

    # ---- loop ----------------------------------------------------------
    def start(self):
        if self._task and not self._task.done():
            return
        if self._arm_deadline is None:
            # A schedule that is already due (backend was down past its next
            # run) fires now instead of waiting another whole interval.
            base = self._last_run_at or datetime.utcnow()
            self._arm(
                max(datetime.utcnow(), base + timedelta(seconds=self.interval_seconds))
            )
        try:
            self._task = asyncio.create_task(self._loop())
        except RuntimeError:
            # no running loop (sync context): the app lifespan will start it
            self._task = None

    def _arm(self, at: datetime = None) -> datetime:
        """Set the next due time (default: one interval from now)."""
        self._arm_deadline = at or (
            datetime.utcnow() + timedelta(seconds=self.interval_seconds)
        )
        return self._arm_deadline

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._arm_deadline = None

    async def _loop(self):
        while True:
            if self._arm_deadline is None:
                self._arm()
            remaining = max(
                0.0, (self._arm_deadline - datetime.utcnow()).total_seconds()
            )
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=remaining)
                self._wakeup.clear()
                # Woken by update(): it saved the new interval and re-armed the
                # deadline. Only run if that deadline has actually passed; a
                # later one is waited out again.
                if not self.enabled or datetime.utcnow() < self._arm_deadline:
                    if not self.enabled:
                        self._arm()  # park until re-enabled
                    continue
            except asyncio.TimeoutError:
                pass
            if not self.enabled:
                self._arm()
                continue
            try:
                await self._run_once()
            except Exception as exc:  # never let the loop die
                log.warning("scheduled scan failed: %s", exc)
            # The run consumed the deadline: next run counts from now.
            self._arm()

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
