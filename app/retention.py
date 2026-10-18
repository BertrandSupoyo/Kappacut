"""Retention GC — warn, then delete, projects nobody has touched in a while.

Run daily from the arq worker (app/worker.py cron). `retention_delete_days = 0` disables.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import func, select

from app import storage
from app.config import settings
from app.db import async_session_maker
from app.email import send_retention_warning
from app.models.job import Job
from app.models.project import Project
from app.models.user import User

log = logging.getLogger("clipfinder.retention")


# Abandoned tusd partials used to be swept from the local staging dir here. tusd writes
# straight to the bucket now, so that job belongs to an S3 lifecycle rule on the `uploads/`
# prefix (expire incomplete multipart uploads + objects after 24h) — configured once on the
# bucket, not re-implemented on a cron. See PLAN-v2.md Phase A.


def _idle_before(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


async def run_gc() -> dict:
    if settings.retention_delete_days <= 0:
        return {"disabled": True}

    warned = deleted = skipped = objects = 0
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
            objects += await asyncio.to_thread(storage.delete_project_media, p.user_id, p.id)
            await s.delete(p)  # cascades analyses / edits / jobs / outputs
            deleted += 1
        await s.commit()

    log.info("retention GC: warned=%d deleted=%d (%d objects) skipped=%d",
             warned, deleted, objects, skipped)
    return {"warned": warned, "deleted": deleted, "objects": objects, "skipped": skipped}
