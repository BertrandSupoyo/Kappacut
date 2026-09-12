"""Retention GC — warn, then delete, projects nobody has touched in a while.

Run daily from the arq worker (app/worker.py cron). `retention_delete_days = 0` disables.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from sqlalchemy import func, select

from app import storage
from app.config import settings
from app.db import async_session_maker
from app.email import send_retention_warning
from app.models.job import Job
from app.models.project import Project
from app.models.user import User

log = logging.getLogger("clipfinder.retention")


def _sweep_stale_uploads(older_than_s: int = 24 * 3600) -> int:
    """Drop tusd partials that were abandoned mid-upload (tusd has no expiry flag here)."""
    d = storage.MEDIA / "_uploads"
    if not d.is_dir():
        return 0
    cutoff = time.time() - older_than_s
    n = 0
    for f in d.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


def _idle_before(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


async def run_gc() -> dict:
    stale_uploads = _sweep_stale_uploads()
    if settings.retention_delete_days <= 0:
        return {"disabled": True, "stale_uploads": stale_uploads}

    warned = deleted = skipped = 0
    now = dt.datetime.now(dt.UTC)
    idle = func.coalesce(Project.last_opened_at, Project.created_at)

    async with async_session_maker() as s:
        # 1) warn — past the warn line but not yet past the delete line
        to_warn = (await s.execute(
            select(Project).where(
                idle < _idle_before(settings.retention_warn_days),
                idle >= _idle_before(settings.retention_delete_days),
                Project.retention_warned_at.is_(None),
            )
        )).scalars().all()
        for p in to_warn:
            user = await s.get(User, p.user_id)
            if user:
                await send_retention_warning(
                    user.email, p.name,
                    max(1, settings.retention_delete_days - settings.retention_warn_days),
                )
            p.retention_warned_at = now
            warned += 1
        await s.commit()

        # 2) delete
        to_delete = (await s.execute(
            select(Project).where(idle < _idle_before(settings.retention_delete_days))
        )).scalars().all()
        for p in to_delete:
            active = await s.scalar(
                select(func.count()).select_from(Job).where(
                    Job.project_id == p.id, Job.status.in_(("queued", "running"))
                )
            )
            if active:
                skipped += 1
                continue
            storage.delete_project_media(p.user_id, p.id)
            await s.delete(p)  # cascades analyses / edits / jobs / outputs
            deleted += 1
        await s.commit()

    log.info("retention GC: warned=%d deleted=%d skipped=%d stale_uploads=%d",
             warned, deleted, skipped, stale_uploads)
    return {"warned": warned, "deleted": deleted, "skipped": skipped,
            "stale_uploads": stale_uploads}
