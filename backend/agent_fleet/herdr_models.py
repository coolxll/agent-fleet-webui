"""Last observation and last good, allowlisted Herdr data per runtime."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agent_fleet.db import Base


class HerdrObservation(Base):
    __tablename__ = "herdr_observations"

    runtime_id: Mapped[str] = mapped_column(String, ForeignKey("runtimes.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String, default="unknown")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    workspaces: Mapped[list] = mapped_column(JSON, default=list)
    sessions: Mapped[list] = mapped_column(JSON, default=list)
    sessions_status: Mapped[str] = mapped_column(String, default="unknown")
    sessions_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sessions_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
