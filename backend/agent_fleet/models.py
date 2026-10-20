"""Domain entities.

Identity of an agent installation is (host, runtime, user, agent).

A Runtime is an execution environment reachable at a given SSH target:
- a macOS/Linux/Windows host has a "native" primary runtime
- a Windows host can additionally expose WSL runtimes, each with its own
  distro, user, and independent agent installs + configs
Remote raw config text is never stored; only sanitized, normalized values are.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_fleet.db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.utcnow()


def iso_utc(value) -> str | None:
    """Serialize a naive-UTC datetime for the API (browsers need the Z)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    return str(value)


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    hostname: Mapped[str | None] = mapped_column(String, nullable=True)
    inventory_source: Mapped[str] = mapped_column(String, default="project-yaml")
    group: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # A host can be disabled (for example an SSH alias you do not want in the
    # fleet) without deleting its history.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    runtimes: Mapped[list["Runtime"]] = relationship(
        back_populates="host", cascade="all, delete-orphan"
    )


class Runtime(Base):
    __tablename__ = "runtimes"
    __table_args__ = (UniqueConstraint("host_id", "key", name="uq_runtime_host_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(
        String, ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="unknown")
    label: Mapped[str] = mapped_column(String, default="")

    transport: Mapped[str] = mapped_column(String, default="ssh")
    ssh_alias: Mapped[str | None] = mapped_column(String, nullable=True)
    ssh_host: Mapped[str | None] = mapped_column(String, nullable=True)
    ssh_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collect_user: Mapped[str | None] = mapped_column(String, nullable=True)
    wsl_distro: Mapped[str | None] = mapped_column(String, nullable=True)
    wsl_user: Mapped[str | None] = mapped_column(String, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    os_name: Mapped[str | None] = mapped_column(String, nullable=True)
    arch: Mapped[str | None] = mapped_column(String, nullable=True)
    home_dir: Mapped[str | None] = mapped_column(String, nullable=True)

    reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_snapshot_id: Mapped[str | None] = mapped_column(String, nullable=True)

    host: Mapped["Host"] = relationship(back_populates="runtimes")
    installations: Mapped[list["AgentInstallation"]] = relationship(
        back_populates="runtime", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="runtime", cascade="all, delete-orphan"
    )

    @property
    def runtime_identity(self) -> str:
        return f"{self.host.name if self.host else '?'}/{self.key}"


class AgentInstallation(Base):
    __tablename__ = "agent_installations"
    __table_args__ = (UniqueConstraint("runtime_id", "agent_id", name="uq_runtime_agent"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    runtime_id: Mapped[str] = mapped_column(
        String, ForeignKey("runtimes.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(String, index=True)

    # installed | not_installed | config_only | unknown | error | not_scanned
    status: Mapped[str] = mapped_column(String, default="not_scanned")
    detected: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    version_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    exec_path: Mapped[str | None] = mapped_column(String, nullable=True)
    install_method: Mapped[str | None] = mapped_column(String, nullable=True)
    config_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # parsed | partial | empty | unsupported | error | not_scanned
    config_status: Mapped[str] = mapped_column(String, default="not_scanned")
    config_id: Mapped[str | None] = mapped_column(String, nullable=True)

    runtime: Mapped["Runtime"] = relationship(back_populates="installations")
    config: Mapped["AgentConfiguration | None"] = relationship(
        "AgentConfiguration",
        primaryjoin="AgentInstallation.id==AgentConfiguration.installation_id",
        viewonly=True,
        uselist=False,
    )
    sources: Mapped[list["ConfigSource"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )


class ConfigSource(Base):
    """Where a config fragment came from: scope, path, format, readability."""

    __tablename__ = "config_sources"
    __table_args__ = (UniqueConstraint("installation_id", "path", name="uq_source_path"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    installation_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_installations.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String, default="user")
    path: Mapped[str] = mapped_column(String)
    format: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String, default="config")
    present: Mapped[bool] = mapped_column(Boolean, default=False)
    readable: Mapped[bool] = mapped_column(Boolean, default=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ok | missing | unreadable | parse_error | unsupported | unknown
    status: Mapped[str] = mapped_column(String, default="unknown")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    installation: Mapped["AgentInstallation"] = relationship(back_populates="sources")


class AgentConfiguration(Base):
    """Sanitized, normalized configuration for one (runtime, agent) pair.

    Only whitelisted fields survive. Unknown keys are dropped, never stored.
    The resolution field is "declared" unless an adapter proves full precedence
    resolution - we never advertise a partial read as the effective config.
    """

    __tablename__ = "agent_configurations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    installation_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_installations.id", ondelete="CASCADE"), index=True
    )

    # declared | effective | unknown
    resolution: Mapped[str] = mapped_column(String, default="declared")
    # parsed | partial | empty | unsupported | error
    parse_status: Mapped[str] = mapped_column(String, default="empty")

    model: Mapped[str | None] = mapped_column(String, nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    base_url_masked: Mapped[bool] = mapped_column(Boolean, default=False)

    mcp_servers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Optional sanitized account-quota snapshot (agy-swap and similar).
    quota: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    unsupported_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sanitized: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String, default="running")
    triggered_by: Mapped[str] = mapped_column(String, default="manual")
    target_runtime_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    concurrency: Mapped[int] = mapped_column(Integer, default=4)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScanTask(Base):
    __tablename__ = "scan_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("scan_runs.id", ondelete="CASCADE"), index=True
    )
    runtime_id: Mapped[str] = mapped_column(String, index=True)
    # pending | running | ok | failed | timeout | skipped
    status: Mapped[str] = mapped_column(String, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    agents_found: Mapped[int] = mapped_column(Integer, default=0)


class AppState(Base):
    """Tiny key/value store for runtime configuration that must survive restart."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Snapshot(Base):
    """Historical record of one successful runtime collection."""

    __tablename__ = "snapshots"
    __table_args__ = (Index("ix_snapshot_runtime_time", "runtime_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    runtime_id: Mapped[str] = mapped_column(
        String, ForeignKey("runtimes.id", ondelete="CASCADE"), index=True
    )
    scan_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agents_found: Mapped[int] = mapped_column(Integer, default=0)


class Session(Base):
    """A conversation/session record belonging to an agent in a runtime."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint(
            "runtime_id", "agent_id", "session_key", name="uq_session_runtime_agent_key"
        ),
        Index("ix_session_runtime_updated", "runtime_id", "updated_at"),
        Index("ix_session_agent_updated", "agent_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    runtime_id: Mapped[str] = mapped_column(
        String, ForeignKey("runtimes.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(String, index=True)
    session_key: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String, default="")
    project_path: Mapped[str | None] = mapped_column(String, nullable=True)
    project_name: Mapped[str | None] = mapped_column(String, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    runtime: Mapped["Runtime"] = relationship(back_populates="sessions")
    transcript: Mapped["SessionTranscript | None"] = relationship(
        back_populates="session", uselist=False, cascade="all, delete-orphan"
    )


class SessionTranscript(Base):
    """Locally cached, sanitized conversation turns for a session."""

    __tablename__ = "session_transcripts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, index=True
    )
    turns: Mapped[list | None] = mapped_column(JSON, nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_secrets_masked: Mapped[bool] = mapped_column(Boolean, default=False)
    masked_count: Mapped[int] = mapped_column(Integer, default=0)
    cached_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped["Session"] = relationship(back_populates="transcript")

