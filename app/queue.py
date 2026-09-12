"""Enqueue side of the arq job queue (used by the web process)."""
from __future__ import annotations

import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def enqueue_analyze(job_id: uuid.UUID) -> str | None:
    job = await (await get_pool()).enqueue_job("analyze_task", str(job_id))
    return job.job_id if job else None


async def enqueue_render(job_id: uuid.UUID) -> str | None:
    job = await (await get_pool()).enqueue_job("render_task", str(job_id))
    return job.job_id if job else None


async def abort_job(arq_job_id: str) -> bool:
    from arq.jobs import Job as ArqJob

    try:
        return await ArqJob(arq_job_id, await get_pool()).abort(timeout=5)
    except Exception:  # noqa: BLE001
        return False
