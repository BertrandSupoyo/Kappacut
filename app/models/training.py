"""Hand-marked moments for measuring the clip picker, and the results of each training run."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class VideoLabel(Base):
    """The moments someone marked as worth a clip in one video. One row per project; times are
    in the source video, so they survive a re-analysis, but not a replaced source (which is what
    `source_bytes` is for: a label for a different file must not be scored)."""

    __tablename__ = "video_labels"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    moments: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)   # [{start, end, note}]
    source_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)   # every good moment is marked
    blind: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # marked before seeing the AI's picks
    revealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # the picks have been shown here
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TrainingRun(Base):
    """One press of Run training: the score at that moment and what the trainer found, kept so
    a prompt or model change can be compared with the runs before it. Adopts nothing."""

    __tablename__ = "training_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    score: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
