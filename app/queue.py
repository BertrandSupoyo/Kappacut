"""Enqueue side of the arq job queue (used by the web process)."""
from __future__ import annotations

import logging
import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from redis.exceptions import RedisError

from app.config import settings

log = logging.getLogger("kappacut.queue")

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def _enqueue(task: str, job_id: uuid.UUID) -> str | None:
    """Queue a job whose row is already committed. The row is the record of the work and
    this is only the fast path, so a Redis outage here isn't the request's failure: raising
    would answer 500 for a job that exists, and leave it blocking the user's next one.
    app.jobs.recover_queued puts it on the queue once Redis is back."""
    try:
        job = await (await get_pool()).enqueue_job(task, str(job_id))
    except (RedisError, OSError) as e:
        log.warning("couldn't queue %s for job %s (%s); the sweep will", task, job_id, e)
        return None
    return job.job_id if job else None


async def enqueue_analyze(job_id: uuid.UUID) -> str | None:
    return await _enqueue("analyze_task", job_id)


async def enqueue_render(job_id: uuid.UUID) -> str | None:
    return await _enqueue("render_task", job_id)
