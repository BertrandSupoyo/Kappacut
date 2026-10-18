"""arq worker — runs analyze / render jobs off the Redis queue.

  arq app.worker.WorkerSettings          (prod)
  arq app.worker.WorkerSettings --watch app   (dev reload)

Must not import app.main (no FastAPI here) — only models / db / pipeline / storage /
jobs / groq_gate.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import uuid

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select, text, update

from app import groq_gate
from app.config import settings
from app.db import async_session_maker, engine
from app.jobs import run_analyze, run_render
from app.models.job import Job
from app.models.project import Analysis, Project
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


async def _wait_for_schema(poll_s: float = 2.0, log_every: int = 15) -> None:
    """Block until migrations have created the tables startup touches.

    `alembic upgrade head` is a manual step run after `compose up`, so on a fresh DB the
    worker can boot first. Querying `jobs` then raises inside arq's startup hook, and under
    `--watch` that leaves a live container with no worker loop — jobs queue forever.
    """
    for attempt in itertools.count(1):
        async with engine.connect() as conn:
            missing = (await conn.execute(text(
                "SELECT to_regclass('jobs') IS NULL OR to_regclass('projects') IS NULL"
            ))).scalar()
        if not missing:
            return
        if attempt == 1 or attempt % log_every == 0:
            log.warning("waiting for migrations (jobs/projects tables missing) — "
                        "run `alembic upgrade head`")
        await asyncio.sleep(poll_s)


async def on_startup(ctx: dict) -> None:
    # Under `arq --watch` a raising startup hook only kills an asyncio task: the process
    # (and container) stays up with no worker loop, so queued jobs sit forever looking
    # healthy. Exit hard instead and let the compose restart policy bring us back.
    try:
        await _startup()
    except Exception:  # not BaseException: a --watch reload cancels startup legitimately
        log.exception("worker startup failed — exiting so the container restarts")
        logging.shutdown()
        os._exit(1)


async def _startup() -> None:
    import clipfinder as cf

    settings.check_production_ready()          # same boot guard the web process runs
    await _wait_for_schema()
    groq_gate.install()                       # clipfinder.GROQ_GATE -> shared TPM bucket
    cf.FFMPEG_THREADS = settings.ffmpeg_threads  # cap per-job core usage under concurrency
    await asyncio.to_thread(synth_builtins)   # builtin sfx onto the media volume
    async with async_session_maker() as s:    # any job left 'running' died with a worker
        orphaned = set((await s.execute(
            select(Job.project_id).where(Job.status == "running")
        )).scalars().all())
        res = await s.execute(
            update(Job).where(Job.status == "running")
            .values(status="error", error="worker restarted")
        )
        # The job row is only half the state. A project whose analyze job died still reads
        # 'analyzing', and the SPA polls that forever with no error to show — so fail the
        # ones now left with no live job. A project whose job is merely 'queued' is
        # untouched: arq will still run it after this restart.
        stuck = 0
        if orphaned:
            has_live_job = (
                select(Job.id)
                .where(Job.project_id == Project.id, Job.status.in_(("queued", "running")))
                .exists()
            )
            has_analysis = select(Analysis.id).where(Analysis.project_id == Project.id).exists()
            stranded = (Project.id.in_(orphaned), Project.status.in_(("queued", "analyzing")), ~has_live_job)
            # an interrupted *re*-analysis goes back to its previous clips, not to "failed"
            await s.execute(update(Project).where(*stranded, has_analysis)
                            .values(status="ready", error=None))
            stuck = (await s.execute(
                update(Project).where(*stranded, ~has_analysis)
                .values(status="failed", error="worker restarted")
            )).rowcount or 0
        await s.commit()
        if res.rowcount:
            log.warning("swept %d orphaned running job(s); failed %d stuck project(s)",
                        res.rowcount, stuck)


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
    # On SIGINT/SIGTERM stop taking jobs and let the running ones finish (up to this many
    # seconds) instead of cancelling them. Pairs with compose's stop_grace_period (deploys)
    # and the dev reloader's --sigint-timeout (compose.override.yaml).
    job_completion_wait = 600
    # Heartbeat key read by `arq app.worker.WorkerSettings --check` (compose healthcheck).
    # arq's default is 3600s, far too slow to notice a dead worker loop.
    health_check_interval = 30
