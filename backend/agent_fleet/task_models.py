"""Persistent user work, separate from administrative upgrade jobs."""
from __future__ import annotations

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agent_fleet.db import Base
from agent_fleet.models import new_id, utcnow
from datetime import datetime


class FleetTask(Base):
    __tablename__ = "fleet_tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String, default="")
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="todo", index=True)
    project_path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TaskStage(Base):
    __tablename__ = "task_stages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("fleet_tasks.id", ondelete="CASCADE"), index=True)
    # Unique while executing. SQLite enforces this across simultaneous requests/processes.
    active_task_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    runtime_id: Mapped[str | None] = mapped_column(ForeignKey("runtimes.id", ondelete="SET NULL"), nullable=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)
    source_session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)
    host_name: Mapped[str] = mapped_column(String, default="")
    agent_id: Mapped[str] = mapped_column(String)
    native_session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    project_path: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String, default="handoff")
    status: Mapped[str] = mapped_column(String, default="starting")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
