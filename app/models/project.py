"""Project + its derived analysis + the editor autosave state."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# a project moves: uploading -> queued -> analyzing -> ready  (or -> failed)
PROJECT_STATUSES = ("uploading", "queued", "analyzing", "ready", "failed")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    taste: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_render: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_ext: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    source_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploading", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_opened_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_warned_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Analysis(Base):
    """One row per project (replaced on re-analyze). Today's analysis.json, in a column."""

    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    segments: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    clips: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    spend: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    backend: Mapped[str] = mapped_column(String(16), nullable=False, default="groq")
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProjectEdit(Base):
    """The SPA's autosave target. Today's project.json, in a column."""

    __tablename__ = "project_edits"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
