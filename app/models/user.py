"""User model — fastapi-users' UUID base table plus Kappacut columns."""
from __future__ import annotations

import datetime as dt

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    # case-insensitive unique email (needs the citext extension — created in migration 0001)
    email: Mapped[str] = mapped_column(CITEXT, unique=True, index=True, nullable=False)

    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # manual per-user quota bumps; NULL = use the plan default from Settings
    quota_minutes_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_storage_mb_override: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # which Terms this account agreed to, and when; NULL = made by the admin CLI, or before
    # sign-up asked
    terms_accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_version: Mapped[str | None] = mapped_column(String(16), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
