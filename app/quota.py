"""Per-user quota accounting + enforcement.

Pure functions over an AsyncSession; `check_quota` raises `QuotaError` (mapped to 429 in
app/main.py). Caps come from Settings, overridable per user via the `quota_*_override`
columns. Concurrency + global backpressure live in app/projects.py `_guard_enqueue`.
"""
from __future__ import annotations

import datetime as dt
import shutil

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.job import RenderOutput
from app.models.project import Project
from app.models.usage import UsageEvent
from app.models.user import User


class QuotaError(Exception):
    def __init__(self, limit: str, used: float, cap: float, resets_at: dt.datetime | None = None):
        self.limit = limit
        self.used = used
        self.cap = cap
        self.resets_at = resets_at
        super().__init__(f"quota '{limit}' exceeded ({used}/{cap})")


def minutes_cap(user: User) -> int:
    return int(user.quota_minutes_override or settings.quota_minutes_per_month)


def storage_cap_bytes(user: User) -> int:
    if user.quota_storage_mb_override:
        return int(user.quota_storage_mb_override) * 1024**2
    return int(settings.quota_storage_bytes)


def _month_start() -> dt.datetime:
    now = dt.datetime.now(dt.UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month() -> dt.datetime:
    m = _month_start()
    return (m.replace(day=28) + dt.timedelta(days=7)).replace(day=1)


def _day_start() -> dt.datetime:
    now = dt.datetime.now(dt.UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def assert_disk_ok() -> None:
    """Raise when the media volume is critically low — protects the box from a full disk."""
    try:
        du = shutil.disk_usage(settings.media_root)
        free_pct = du.free / du.total * 100
    except Exception:
        return
    if free_pct < settings.disk_min_free_pct:
        raise QuotaError("server_storage", round(100 - free_pct, 1), 100 - settings.disk_min_free_pct)


async def monthly_minutes(session: AsyncSession, user_id) -> float:
    secs = await session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.kind == "transcribe_seconds",
            UsageEvent.created_at >= _month_start(),
        )
    )
    return round((secs or 0) / 60, 2)


async def storage_bytes(session: AsyncSession, user_id) -> int:
    src = await session.scalar(
        select(func.coalesce(func.sum(Project.source_bytes), 0)).where(Project.user_id == user_id)
    )
    rendered = await session.scalar(
        select(func.coalesce(func.sum(RenderOutput.bytes), 0))
        .select_from(RenderOutput)
        .join(Project, Project.id == RenderOutput.project_id)
        .where(Project.user_id == user_id)
    )
    return int(src or 0) + int(rendered or 0)


async def renders_today(session: AsyncSession, user_id) -> int:
    return int(await session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.kind == "render_clip",
            UsageEvent.created_at >= _day_start(),
        )
    ) or 0)


async def project_count(session: AsyncSession, user_id) -> int:
    return int(await session.scalar(
        select(func.count()).select_from(Project).where(Project.user_id == user_id)
    ) or 0)


async def check_quota(
    session: AsyncSession, user: User, action: str, *, extra_bytes: int = 0, count: int = 1
) -> None:
    if action == "project":
        n = await project_count(session, user.id)
        if n >= settings.quota_projects:
            raise QuotaError("projects", n, settings.quota_projects)

    elif action == "upload":
        assert_disk_ok()
        if extra_bytes > settings.quota_max_upload_bytes:
            raise QuotaError("file_size", extra_bytes, settings.quota_max_upload_bytes)
        cap = storage_cap_bytes(user)
        used = await storage_bytes(session, user.id)
        if used + extra_bytes > cap:
            raise QuotaError("storage", used + extra_bytes, cap)

    elif action == "analyze":
        used = await monthly_minutes(session, user.id)
        cap = minutes_cap(user)
        if used >= cap:
            raise QuotaError("minutes", used, cap, resets_at=_next_month())

    elif action == "render":
        used = await renders_today(session, user.id)
        cap = settings.quota_renders_per_day
        # `count` = how many clips this job will cut. The cap counts CLIPS, not jobs: one
        # request carrying a thousand ranges used to pass a 40-a-day limit untouched and
        # then hold the worker for hours, since only the enqueue was ever checked.
        if used + count > cap:
            raise QuotaError("renders", used + count, cap,
                             resets_at=_day_start() + dt.timedelta(days=1))
