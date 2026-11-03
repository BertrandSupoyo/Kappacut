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
import socket
import uuid
from pathlib import Path

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select, text, update

from app import groq_gate
from app import jobs as jobs_mod
from app.config import settings
from app.db import async_session_maker, engine
from app.jobs import (FANOUT, MAX_ATTEMPTS, owner_worker, release_clip, render_key, requeue,
                      resume_render, retry_or_end_clip, run_analyze, run_render, run_render_clip)
from app.models.job import Job
from app.models.project import Analysis, Project
from app.retention import run_gc
from app.sfx_synth import synth_builtins

log = logging.getLogger("kappacut.worker")
logging.basicConfig(level=logging.INFO)


async def analyze_task(ctx: dict, job_id: str) -> None:
    await run_analyze(uuid.UUID(job_id))


async def render_task(ctx: dict, job_id: str) -> None:
    await run_render(uuid.UUID(job_id))      # only fans out: queues the per-clip tasks


async def render_clip_task(ctx: dict, job_id: str, idx: int) -> None:
    await run_render_clip(uuid.UUID(job_id), int(idx))


async def retention_gc(ctx: dict) -> None:
    await run_gc()


# Each worker process keeps a short-lived Redis key alive while it runs. A 'running' job
# whose worker's key has expired was orphaned by a crash or kill; one whose key is still
# there belongs to a live sibling (another replica, or the process a dev reload is
# replacing while it finishes its job) and must be left alone.
HEARTBEAT_TTL = 45          # three missed beats: a live worker never lapses by accident
HEARTBEAT_EVERY = 15


def _hb_key(worker_id: str) -> str:
    return f"kappacut:worker:{worker_id}"


async def _heartbeat(redis, worker_id: str) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_EVERY)
        try:
            await redis.set(_hb_key(worker_id), "1", ex=HEARTBEAT_TTL)
        except Exception:  # noqa: BLE001 — a Redis blip must not kill the heartbeat
            log.warning("worker heartbeat failed", exc_info=True)


async def sweep_orphans(ctx: dict) -> None:
    """Recover 'running' jobs whose worker is gone: run them again (or the lost clips of a
    render) while they have attempts left; only then fail them and unstick their projects.

    Runs at startup and on a cron, so a job a dead worker left behind is picked up even
    when no worker restarts afterwards.
    """
    redis = ctx["redis"]
    async with async_session_maker() as s:
        running = (await s.execute(
            select(Job.id, Job.project_id, Job.worker_id, Job.kind).where(Job.status == "running")
        )).all()

        async def total_of(jid) -> int:
            return len(((await s.get(Job, jid)).payload or {}).get("ranges") or [])

        async def gone(jid, wid) -> bool:
            # NULL worker_id: started before this column existed — keep the old assumption
            # that whoever took it is gone
            if wid is None:
                return True
            if wid == FANOUT:
                # A render whose clips are spread over workers. A clip whose worker died
                # mid-clip would never end: run it again (it counts as failed only after
                # MAX_ATTEMPTS). The job itself isn't orphaned.
                owners = render_key(jid, "owners")
                held = await redis.hgetall(owners)
                for idx, owner in held.items():
                    idx = int(idx.decode() if isinstance(idx, bytes) else idx)
                    owner = owner.decode() if isinstance(owner, bytes) else owner
                    w = owner_worker(owner)
                    # releasing exactly the hold we saw is the claim: neither a concurrent
                    # sweep nor the clip itself (if only slow, not dead) can end the clip a
                    # second time, and a retry that took it over since keeps its hold
                    if not await redis.exists(_hb_key(w)) and await release_clip(redis, jid, idx, owner):
                        log.warning("render %s: clip %d lost with worker %s", jid, idx + 1, w)
                        await retry_or_end_clip(jid, await total_of(jid), idx,
                                                f"clip {idx + 1}: worker stopped mid-clip")
                # Nothing held and nothing happened for a whole job timeout: the chain broke
                # where no owner was recorded (a clip task lost before claiming its slot).
                # Pick it back up from the clips that never ended.
                if not held and not await redis.exists(render_key(jid, "alive")):
                    await resume_render(jid, await total_of(jid))
                return False
            return not await redis.exists(_hb_key(wid))

        dead = []
        for jid, pid, wid, kind in running:
            if await gone(jid, wid) and not await requeue(jid, kind, "Worker restarted"):
                dead.append((jid, pid))           # out of attempts: fail it below
        if not dead:
            return
        orphaned = {pid for _, pid in dead}
        res = await s.execute(
            update(Job).where(Job.id.in_([jid for jid, _ in dead]), Job.status == "running")
            .values(status="error", error=f"worker restarted (gave up after {MAX_ATTEMPTS} attempts)")
        )
        # The job row is only half the state. A project whose analyze job died still reads
        # 'analyzing', and the SPA polls that forever with no error to show — so fail the
        # ones now left with no live job. A project whose job is merely 'queued' is
        # untouched: arq will still run it.
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


def _migrations() -> tuple[str | None, set[str]]:
    """(newest migration this code ships, every revision it knows); (None, set()) when the
    alembic files aren't next to the app, which leaves only the table check below."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parent.parent
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        script = ScriptDirectory.from_config(cfg)
        return script.get_current_head(), {r.revision for r in script.walk_revisions()}
    except Exception:  # noqa: BLE001
        log.warning("couldn't read the alembic scripts; only checking that tables exist", exc_info=True)
        return None, set()


async def _wait_for_schema(poll_s: float = 2.0, log_every: int = 15) -> None:
    """Block until the database has every migration this code needs.

    `alembic upgrade head` is a manual step run after `compose up`, so the worker can boot
    first: on a fresh DB with no tables, or after an upgrade with a column still missing.
    Querying then raises inside arq's startup hook - a crash loop, or under `--watch` a live
    container with no worker loop, jobs queuing forever.
    """
    head, known = await asyncio.to_thread(_migrations)
    for attempt in itertools.count(1):
        async with engine.connect() as conn:
            missing = (await conn.execute(text(
                "SELECT to_regclass('jobs') IS NULL OR to_regclass('projects') IS NULL"
            ))).scalar()
            current = None
            if not missing and head and (await conn.execute(
                    text("SELECT to_regclass('alembic_version') IS NOT NULL"))).scalar():
                current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        # a revision this code doesn't know is newer than it (a rollback in progress): go on
        behind = bool(head) and current != head and (current is None or current in known)
        if not missing and not behind:
            return
        if attempt == 1 or attempt % log_every == 0:
            log.warning("waiting for migrations (database at %s, this code needs %s) — "
                        "run `alembic upgrade head`", current or "no tables", head or "its tables")
        await asyncio.sleep(poll_s)


async def on_startup(ctx: dict) -> None:
    # Under `arq --watch` a raising startup hook only kills an asyncio task: the process
    # (and container) stays up with no worker loop, so queued jobs sit forever looking
    # healthy. Exit hard instead and let the compose restart policy bring us back.
    try:
        await _startup(ctx)
    except Exception:  # not BaseException: a --watch reload cancels startup legitimately
        log.exception("worker startup failed — exiting so the container restarts")
        logging.shutdown()
        os._exit(1)


async def _startup(ctx: dict) -> None:
    import kappacut as cf

    settings.check_production_ready()          # same boot guard the web process runs
    await _wait_for_schema()
    groq_gate.install()                       # kappacut.GROQ_GATE -> shared TPM bucket
    cf.FFMPEG_THREADS = settings.ffmpeg_threads  # cap per-job core usage under concurrency
    await asyncio.to_thread(synth_builtins)   # builtin sfx onto the media volume
    wid = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    jobs_mod.WORKER_ID = wid
    await ctx["redis"].set(_hb_key(wid), "1", ex=HEARTBEAT_TTL)   # alive before taking jobs
    ctx["heartbeat"] = asyncio.create_task(_heartbeat(ctx["redis"], wid))
    log.info("worker %s up", wid)
    await sweep_orphans(ctx)


async def on_shutdown(ctx: dict) -> None:
    # arq calls this after running jobs finished (job_completion_wait), so nothing of ours
    # is still running: drop the key now and the next sweep needn't wait out the TTL
    if task := ctx.get("heartbeat"):
        task.cancel()
    if jobs_mod.WORKER_ID:
        try:
            await ctx["redis"].delete(_hb_key(jobs_mod.WORKER_ID))
        except Exception:  # noqa: BLE001
            pass
    await engine.dispose()


class WorkerSettings:
    functions = [analyze_task, render_task, render_clip_task]
    cron_jobs = [
        cron(retention_gc, hour=3, minute=0),
        # picks up the jobs / clips of a worker that died (they're re-run, not failed); every
        # minute, so a killed clip is back on the queue within ~1.5 min (heartbeat TTL + this)
        cron(sweep_orphans, minute=set(range(60))),
    ]
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
