"""arq worker — runs analyze / render jobs off the Redis queue.

  arq app.worker.WorkerSettings          (prod)
  arq app.worker.WorkerSettings --watch app   (dev reload)

Must not import app.main (no FastAPI here) — only models / db / pipeline / storage /
jobs / groq_gate.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import update

from app import groq_gate
from app.config import settings
from app.db import async_session_maker, engine
from app.jobs import run_analyze, run_render
from app.models.job import Job
from app.retention import run_gc
from app.sfx_synth import synth_builtins

log = logging.getLogger("clipfinder.worker")
logging.basicConfig(level=logging.INFO)


async def analyze_task(ctx: dict, job_id: str) -> None:
    await run_analyze(uuid.UUID(job_id))


async def render_task(ctx: dict, job_id: str) -> None:
    await run_render(uuid.UUID(job_id))


async def retention_gc(ctx: dict) -> None:
    await run_gc()


async def on_startup(ctx: dict) -> None:
    import clipfinder as cf

    groq_gate.install()                       # clipfinder.GROQ_GATE -> shared TPM bucket
    cf.FFMPEG_THREADS = settings.ffmpeg_threads  # cap per-job core usage under concurrency
    await asyncio.to_thread(synth_builtins)   # builtin sfx onto the media volume
    async with async_session_maker() as s:    # any job left 'running' died with a worker
        res = await s.execute(
            update(Job).where(Job.status == "running")
            .values(status="error", error="worker restarted")
        )
        await s.commit()
        if res.rowcount:
            log.warning("swept %d orphaned running job(s)", res.rowcount)


async def on_shutdown(ctx: dict) -> None:
    await engine.dispose()


class WorkerSettings:
    functions = [analyze_task, render_task]
    cron_jobs = [cron(retention_gc, hour=3, minute=0)]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = on_startup
    on_shutdown = on_shutdown
    max_jobs = settings.worker_concurrency
    job_timeout = settings.job_timeout_seconds
    max_tries = 1            # a failed job is failed — the user re-triggers, no silent retry
    keep_result = 300
