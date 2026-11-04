"""Run analyze / render off the request path, writing progress to the `jobs` row.

Called from the arq worker (app/worker.py). `pipeline.*` is blocking (ffmpeg + SDK) so it
runs in `asyncio.to_thread`; progress callbacks hop back to the loop via
`call_soon_threadsafe` onto a queue that a drain task commits to Postgres.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from arq.jobs import Job as ArqJob, JobStatus
from sqlalchemy import delete, select, update

import kappacut as cf
import pipeline
from app.db import async_session_maker
from app.models.job import Job, RenderOutput
from app.models.project import Analysis, Project, ProjectEdit
from app.models.usage import UsageEvent
from app.models.user import User
from app.config import settings
from app.queue import enqueue_render, get_pool
from app.quota import check_quota, is_unlimited, minutes_cap, monthly_minutes
from app import storage

log = logging.getLogger("kappacut.jobs")

# Older projects carried "How many clips" and "Content type" inside the taste text as
# "<N> clips." / "Content type: <category>."; still understood for jobs created back then.
_COUNT_RE = re.compile(r"(?:^|\s)(\d{1,2}) clips\.")
_TYPE_RE = re.compile(r"Content type: ([a-z]+)\.", re.IGNORECASE)


def _apply_options(cfg: dict, payload: dict | None) -> dict:
    """Fold an analyze job's New-project options into the engine config.

    count        -> the MAXIMUM number of clips (weak ones are not padded in to reach it)
    content_type -> skip genre detection and use that categories.toml profile
    taste        -> the user's own steering, which beats the genre's default taste - but
                    only when it says more than the (legacy) count / type phrases
    """
    payload = payload or {}
    taste = str(payload.get("taste") or "")
    count = payload.get("count")
    if count is None and (m := _COUNT_RE.search(taste)):
        count = m.group(1)
    if count is not None:
        cfg["count"] = cfg["min_clips"] = max(1, min(int(count), 20))
        cfg["count_explicit"] = True
    ctype = payload.get("content_type")
    if not ctype and (t := _TYPE_RE.search(taste)):
        ctype = t.group(1)
    if ctype:
        cfg["category_override"] = str(ctype).lower()
    if _TYPE_RE.sub("", _COUNT_RE.sub("", taste)).strip():
        cfg["taste"] = taste
        cfg["taste_from_user"] = True
    return cfg


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


_LIVE = ("queued", "running")

# Set by app.worker at startup; stamped on every job this process starts.
WORKER_ID: str | None = None


async def _set_job(job_id: uuid.UUID, **fields) -> bool:
    """Update a job row only while it is still live; returns whether it matched.

    A worker reload can't stop the pipeline thread, so a job the startup sweep already
    marked 'error' keeps reporting progress (and may finish). Those late writes must not
    resurrect the row — that's how a job ended up 'error' at 100% "done".
    """
    async with async_session_maker() as s:
        res = await s.execute(
            update(Job).where(Job.id == job_id, Job.status.in_(_LIVE)).values(**fields)
        )
        await s.commit()
    return bool(res.rowcount)


async def _fail(job_id: uuid.UUID, error: str, project_id: uuid.UUID | None = None) -> None:
    """Terminal failure for a job (and the project it belongs to, when analyze owns it).

    Every path out of a job has to land here: a row left at 'running' counts against
    `quota_concurrent_jobs` forever, which locks the user out of starting anything new
    until a worker restart sweeps it.
    """
    # An already-ended job leaves the project alone: it may have a newer job running.
    if await _set_job(job_id, status="error", error=error[:2000], finished_at=_now()) \
            and project_id is not None:
        async with async_session_maker() as s:
            # a failed *re*-analysis must not hide the clips the project already has: keep it
            # ready on its previous analysis (the editor shows the job's error as a banner)
            has_analysis = await s.scalar(
                select(Analysis.id).where(Analysis.project_id == project_id).limit(1))
            await s.execute(update(Project).where(Project.id == project_id).values(
                status="ready" if has_analysis else "failed",
                error=None if has_analysis else error[:2000],
            ))
            await s.commit()


# --------------------------------------------------------------------- retries
#
# A job that fails on a blip (connection drop, API timeout or 5xx, a worker that died or
# restarted under it) goes back on the queue instead of failing: up to MAX_ATTEMPTS runs,
# RETRY_DELAYS apart. Only errors a retry can't fix fail at once - Groq's daily cap, a
# missing source, a deleted project - or a blip that keeps happening MAX_ATTEMPTS times.

MAX_ATTEMPTS = 3
RETRY_DELAYS = (15, 60)           # before the 2nd and the 3rd run
_TRANSIENT_NAMES = {
    "APIConnectionError", "APITimeoutError", "InternalServerError", "ServiceUnavailableError",
    "OverloadedError", "RateLimitError", "ConnectionError", "ConnectionResetError",
    "TimeoutError", "ReadTimeout", "ConnectTimeout", "ReadTimeoutError", "ConnectTimeoutError",
    "EndpointConnectionError", "RemoteDisconnected", "ProtocolError", "ClientConnectorError",
    "OperationalError", "InterfaceError", "ConnectionDoesNotExistError",
}
# model hiccups kappacut raises as RuntimeError: a fresh run usually gets a usable answer
_TRANSIENT_MESSAGES = ("returned no usable answer", "rate limit did not clear")


def retriable(e: BaseException) -> bool:
    """Would running the job again plausibly succeed?"""
    if type(e).__name__ == "GroqQuotaError":           # the daily cap: hours, not seconds
        return False
    for x in (e, e.__cause__, e.__context__):
        if x is None:
            continue
        if type(x).__name__ in _TRANSIENT_NAMES:
            return True
        if (getattr(x, "status_code", 0) or 0) >= 500:
            return True
    return any(m in str(e) for m in _TRANSIENT_MESSAGES)


def _attempts_key(job_id: uuid.UUID | str) -> str:
    return f"kappacut:job:{job_id}:attempts"


async def _begin_attempt(job_id: uuid.UUID) -> int:
    redis = await get_pool()
    n = await redis.incr(_attempts_key(job_id))
    await redis.expire(_attempts_key(job_id), 2 * 86400)
    return int(n)


async def attempts_so_far(job_id: uuid.UUID | str) -> int:
    return int(await (await get_pool()).get(_attempts_key(job_id)) or 0)


async def requeue(job_id: uuid.UUID, kind: str, reason: str) -> bool:
    """Put a live job back on the queue for another run, if it has runs left. Returns False
    when it is out of attempts (or no longer live) and the caller should fail it."""
    done = await attempts_so_far(job_id)
    if done >= MAX_ATTEMPTS:
        return False
    delay = RETRY_DELAYS[min(max(done - 1, 0), len(RETRY_DELAYS) - 1)]
    if not await _set_job(job_id, status="queued", worker_id=None,
                          progress_msg=f"{reason} - retrying (attempt {done + 1} of {MAX_ATTEMPTS})",
                          stage=f"{reason} - retrying"):
        return False
    task = "analyze_task" if kind == "analyze" else "render_task"
    arq_job = await (await get_pool()).enqueue_job(task, str(job_id), _defer_by=delay)
    if arq_job:
        await _set_job(job_id, arq_job_id=arq_job.job_id)   # admin cancel aborts the right task
    log.warning("%s %s: %s - retry %d/%d in %ds", kind, job_id, reason, done + 1, MAX_ATTEMPTS, delay)
    return True


def _short_reason(e: BaseException) -> str:
    n = type(e).__name__
    return "Connection problem" if "Connection" in n or "Timeout" in n else "Temporary problem"


# A 'queued' row counts as the user's one active job, but only a queue entry can run it. If
# the entry never existed (Redis was down at enqueue, the web process died just after its
# commit) or is gone (Redis lost its data, the task died before claiming the row), nothing
# ever would, and the user is told "you already have a job running" until an admin steps in.
# The sweep puts such rows back on the queue. An old copy of the task that turns out to be
# still out there does no harm: _claim lets only one of them start the job.
QUEUED_GRACE_SECONDS = 60         # the request that made the row may be about to enqueue it
_RECOVER_LOCK_SECONDS = 120       # one sweep at a time per job, and time for a new entry to run


async def recover_queued() -> int:
    """Re-queue 'queued' jobs the queue doesn't know about; returns how many. After
    MAX_ATTEMPTS of those a job is failed, so a task that keeps dying before it can claim
    the row still releases the user."""
    redis = await get_pool()
    async with async_session_maker() as s:
        rows = (await s.execute(
            select(Job.id, Job.kind, Job.project_id, Job.arq_job_id).where(
                Job.status == "queued",
                Job.created_at < _now() - dt.timedelta(seconds=QUEUED_GRACE_SECONDS))
        )).all()
    recovered = 0
    for job_id, kind, project_id, arq_id in rows:
        if arq_id and await ArqJob(arq_id, redis).status() in (
                JobStatus.queued, JobStatus.deferred, JobStatus.in_progress):
            continue
        if not await redis.set(f"kappacut:job:{job_id}:recovering", 1, ex=_RECOVER_LOCK_SECONDS, nx=True):
            continue
        tries_key = f"kappacut:job:{job_id}:recoveries"
        tries = await redis.incr(tries_key)
        await redis.expire(tries_key, 2 * 86400)
        if tries > MAX_ATTEMPTS:
            log.error("%s %s: never got running after %d re-queues", kind, job_id, MAX_ATTEMPTS)
            await _fail(job_id, "couldn't get this job started - please try again",
                        project_id if kind == "analyze" else None)
            continue
        task = "analyze_task" if kind == "analyze" else "render_task"
        arq_job = await redis.enqueue_job(task, str(job_id))
        if arq_job:
            await _set_job(job_id, arq_job_id=arq_job.job_id)   # admin cancel aborts the right task
        log.warning("%s %s: not on the queue - re-queued (%d of %d)", kind, job_id, tries, MAX_ATTEMPTS)
        recovered += 1
    return recovered


async def _retry_or_fail(job_id: uuid.UUID, kind: str, e: BaseException,
                         project_id: uuid.UUID | None = None) -> None:
    """Queue the job again for a blip, else fail it. The requeue itself needs Redis, which
    may be the thing that just failed: then the job fails instead of staying 'running'."""
    try:
        if retriable(e) and await requeue(job_id, kind, _short_reason(e)):
            return
    except Exception:  # noqa: BLE001
        log.warning("%s %s: couldn't queue a retry", kind, job_id, exc_info=True)
    await _fail(job_id, f"{type(e).__name__}: {e}", project_id)


async def _claim(s, job_id: uuid.UUID) -> bool:
    """queued -> running in one statement. Reading the status and then writing it let two
    copies of a task (two sweeps re-queue the same orphan) both start the job and pay for
    transcription twice; under this UPDATE the second one matches no row."""
    return bool((await s.execute(
        update(Job).where(Job.id == job_id, Job.status == "queued")
        .values(status="running", started_at=_now(), worker_id=WORKER_ID)
    )).rowcount)


# --------------------------------------------------------------------- timeouts
#
# arq ends a job that runs past job_timeout_seconds (or one an admin aborts) by cancelling
# its coroutine. That used to leave the row 'running' under a worker that is still alive,
# which sweep_orphans never reclaims - the user was locked out of new jobs until a worker
# restart - while the pipeline thread and its ffmpeg ran on. A shutdown cancels jobs the
# same way, but those the next worker's sweep re-runs, so only a timeout is failed here.

def _timed_out(started: float) -> bool:
    return time.monotonic() - started >= settings.job_timeout_seconds - 30


def _timeout_msg() -> str:
    return f"stopped after {settings.job_timeout_seconds // 60} minutes, the per-job time limit"


async def _in_thread(scope: str, fn, *args):
    """Run blocking pipeline work in a thread, its ffmpeg processes registered under
    `scope` so a cancelled job can stop them (kappacut.cancel_scope)."""
    def scoped():
        with cf.proc_scope(scope):
            return fn(*args)

    return await asyncio.to_thread(scoped)


def _stageable_sfx(ranges: list[dict]) -> list[str]:
    """The sfx filenames a render references, deduped — bare names only, the same rule
    kappacut.mix_sfx enforces, so a crafted `file` can't steer the copy out of the
    staging dir (which would raise out of the staging loop and wedge the job)."""
    names: list[str] = []
    for r in ranges:
        for s_ in (r.get("sfx") or []):
            fn = (s_ or {}).get("file")
            if isinstance(fn, str) and fn and Path(fn).name == fn and fn not in names:
                try:
                    storage.builtin_sfx_key(fn)       # a name no key can carry: skip, don't fail the clip
                except ValueError:
                    continue
                names.append(fn)
    return names


def _clip_to_range(c: dict) -> dict:
    """The server-side equivalent of the frontend's keptRanges() (editor/store.ts) — built
    from a clip's `auto` suggestion (kappacut.py:_auto_edit) since there's no ProjectEdit
    yet in auto-render mode. Every clip counts as kept (there's no user edit state to say
    otherwise) and captions default on, mirroring editor/types.ts's defaultEdit()."""
    auto = c.get("auto") or {}
    vertical = bool(auto.get("vertical"))
    crop = auto.get("crop") or {}
    caption = auto.get("caption") or {}
    color = auto.get("color") or {}
    color_set = bool(color) and (
        color.get("preset", "none") != "none" or color.get("b", 0) != 0
        or color.get("c", 1) != 1 or color.get("s", 1) != 1
    )
    return {
        "start": c["start_seconds"],
        "end": c["end_seconds"],
        "vertical": vertical,
        "label": c.get("title") or c.get("hook") or "clip",
        "crop": ({
            "mode": crop.get("mode", "crop"), "px": crop.get("px", 0.5),
            "py": crop.get("py", 0.42), "zoom": crop.get("zoom", 1), "blur": crop.get("blur", 24),
            # follow the detected face, as the editor does when "Follow face" is on
            **({"track": (c.get("face") or {}).get("track")}
               if crop.get("follow") and (c.get("face") or {}).get("track") else {}),
        } if vertical else None),
        "captions": {
            "style": caption.get("style", "clean"), "position": "bottom",
            "size": 1, "anim": caption.get("anim", "none"), "karaoke": bool(caption.get("karaoke")),
        },
        "color": color if color_set else None,
        "sfx": [{"file": s["file"], "at": s.get("at", 0), "gain": s.get("gain", -4)}
                for s in (auto.get("sfx") or [])],
    }


async def _try_auto_render(project_id: uuid.UUID, user_id: uuid.UUID, clips: list[dict]) -> None:
    """Fires right after a successful analyze when Project.auto_render is set. Never allowed
    to affect analyze's own success/failure bookkeeping — any problem here is logged and
    skipped, not raised, since analyze already completed by the time this runs."""
    from app.projects import _guard_enqueue  # local import: avoids a jobs<->projects import cycle

    try:
        ranges = [_clip_to_range(c) for c in clips]
        if not ranges:
            return
        async with async_session_maker() as s:
            user = await s.get(User, user_id)
            await _guard_enqueue(s, user)
            await check_quota(s, user, "render", count=len(ranges))
            render_job = Job(user_id=user_id, project_id=project_id, kind="render",
                              status="queued", payload={"ranges": ranges})
            s.add(render_job)
            await s.commit()
            render_job.arq_job_id = await enqueue_render(render_job.id)
            await s.commit()
        log.info("auto-render: enqueued render %s for project %s (%d clips)",
                  render_job.id, project_id, len(ranges))
    except Exception as e:  # noqa: BLE001
        log.warning("auto-render skipped for project %s: %s", project_id, e)


# --------------------------------------------------------------------- analyze

async def _minutes_short(user_id: uuid.UUID, src_key: str) -> str | None:
    """Why this video doesn't fit the user's remaining monthly minutes, or None if it does.

    The request handlers can only check "minutes left > 0" - nobody knows the length until
    the file is here - so someone with one minute left could analyse three hours. ffprobe
    reads only the container header through a signed URL, before anything is downloaded
    or transcribed. Fails open: an unreadable length leaves the decision to the old check.
    """
    async with async_session_maker() as s:
        user = await s.get(User, user_id)
        if user is None or is_unlimited(user):
            return None
        used, cap = await monthly_minutes(s, user_id), minutes_cap(user)
    try:
        secs = await _in_thread(f"probe:{src_key}", cf.probe_duration,
                                storage.presign_internal(src_key, ttl=300))
    except Exception:  # noqa: BLE001
        log.warning("couldn't read the length of %s; minutes not pre-checked", src_key, exc_info=True)
        return None
    need = secs / 60
    if used + need <= cap:
        return None
    # rounded so the numbers explain the refusal: never "0 min long", never more left than there is
    return (f"This video is {max(1, math.ceil(need))} min long and you have "
            f"{max(0, math.floor(cap - used))} of your {cap} monthly minutes left.")


async def run_analyze(job_id: uuid.UUID) -> None:
    started = time.monotonic()
    scope = f"analyze:{job_id}"
    async with async_session_maker() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        if job.status != "queued":    # cancelled while waiting (e.g. for a retry): stay ended
            return
        project = await s.get(Project, job.project_id)
        if project is None:           # deleted while the job sat in the queue
            await _fail(job_id, "project no longer exists")
            return
        user_id, project_id = project.user_id, project.id
        job_payload = dict(job.payload or {})
        auto_render = project.auto_render
        video_title = project.name or project.source_filename or ""
        if not await _claim(s, job_id):
            return                    # a duplicate task (two sweeps re-queued it) got here first
        project.status = "analyzing"
        await s.commit()

    try:
        attempt = await _begin_attempt(job_id)
        src_key = await asyncio.to_thread(storage.find_source, user_id, project_id)
        short = await _minutes_short(user_id, src_key) if src_key else None
    except Exception as e:  # noqa: BLE001
        # outside the main try below: without this a storage or DB blip here left the row
        # 'running' under a live worker, which the sweep never reclaims
        log.exception("analyze %s failed to start", project_id)
        await _retry_or_fail(job_id, "analyze", e, project_id)
        return
    if src_key is None:
        await _fail(job_id, "source file missing", project_id)
        return
    if short:
        await _fail(job_id, short, project_id)
        return

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def on_progress(pct: int, msg: str) -> None:
        loop.call_soon_threadsafe(q.put_nowait, (int(pct), str(msg)))

    async def drain() -> None:
        # Coalesce to the newest update, then pause. Dropping updates that landed inside
        # the throttle window (the old behaviour) could lose "Transcribing…" right before
        # the longest step, leaving the bar stuck on a stale value for minutes.
        while True:
            item = await q.get()
            while item is not None and not q.empty():
                item = q.get_nowait()
            if item is None:
                return
            pct, msg = item
            try:
                await _set_job(job_id, progress_pct=pct, progress_msg=msg)
            except Exception:  # noqa: BLE001
                # a lost progress tick is cosmetic. Raised, it ended this task and re-raised
                # at `await drainer` after the analysis had finished: the job retried and
                # transcription and picking were paid for a second time.
                log.warning("analyze %s: progress update failed", job_id, exc_info=True)
            await asyncio.sleep(0.4)

    drainer = asyncio.create_task(drain())
    # ffmpeg and Whisper both need a seekable local file, so the source comes down once
    # into a temp dir this job owns and that goes away with it — including on a crash.
    tmp = tempfile.TemporaryDirectory(prefix="kappacut_analyze_")
    try:
        on_progress(2, "Fetching your video")      # a 2 GB download used to sit at 0%
        src = await asyncio.to_thread(
            storage.download, src_key, Path(tmp.name) / Path(src_key).name
        )
        cfg = _apply_options(pipeline.get_cfg(), job_payload)   # fresh dict per call
        cfg["video_title"] = video_title            # "Try Not To Laugh…" says more than any transcript line
        result = await _in_thread(scope, pipeline.analyze, src, cfg, on_progress)
        q.put_nowait(None)
        await drainer

        async with async_session_maker() as s:
            # Claim the finish first: if the job was ended under us (worker restart sweep),
            # drop the result rather than flip a failed project back to 'ready'.
            dur = float(result.get("duration") or 0.0)
            claimed = (await s.execute(
                update(Job).where(Job.id == job_id, Job.status == "running").values(
                    status="done", progress_pct=100, progress_msg="done",
                    finished_at=_now(),
                    result={"duration": dur, "clips": len(result["clips"]),
                            # lets the review screen say "3 strong moments (you asked for 5)"
                            "requested": cfg["count"] if cfg.get("count_explicit") else None},
                )
            )).rowcount
            if not claimed:
                log.warning("analyze %s finished after its job was ended — result discarded",
                            project_id)
                return
            # Saved edits are keyed by clip *index*; after a re-analysis index 3 is a different
            # moment, so old trims/framing would land on the wrong clips. Drop them in the same
            # transaction as the new analysis - a failed re-run keeps both untouched.
            await s.execute(delete(ProjectEdit).where(ProjectEdit.project_id == project_id))
            existing = (
                await s.execute(select(Analysis).where(Analysis.project_id == project_id))
            ).scalar_one_or_none()
            if existing:
                existing.segments = result["segments"]
                existing.clips = result["clips"]
                existing.spend = result.get("spend")
                existing.backend = cfg["backend"]
                existing.category = result.get("category")
            else:
                s.add(Analysis(
                    project_id=project_id,
                    segments=result["segments"], clips=result["clips"],
                    spend=result.get("spend"), backend=cfg["backend"],
                    category=result.get("category"),
                ))
            await s.execute(update(Project).where(Project.id == project_id).values(
                status="ready", duration_seconds=dur, error=None,
            ))
            if dur > 0:
                s.add(UsageEvent(user_id=user_id, project_id=project_id,
                                 kind="transcribe_seconds", quantity=int(round(dur))))
            await s.commit()
        log.info("analyze %s done — %d clips", project_id, len(result["clips"]))
        if auto_render and result["clips"]:
            await _try_auto_render(project_id, user_id, result["clips"])
    except asyncio.CancelledError:
        cf.cancel_scope(scope)
        q.put_nowait(None)
        drainer.cancel()
        if _timed_out(started):
            await asyncio.shield(_fail(job_id, _timeout_msg(), project_id))
        raise
    except Exception as e:  # noqa: BLE001
        q.put_nowait(None)
        with contextlib.suppress(Exception):
            await drainer
        log.exception("analyze %s failed (attempt %d)", project_id, attempt)
        if retriable(e) and await requeue(job_id, "analyze", _short_reason(e)):
            return
        # GroqQuotaError's message is already written for the user ("try again in ~N min")
        msg = str(e) if type(e).__name__ == "GroqQuotaError" else f"{type(e).__name__}: {e}"
        if retriable(e):
            msg += f" (gave up after {attempt} attempts)"
        await _fail(job_id, msg, project_id)
    finally:
        tmp.cleanup()


# --------------------------------------------------------------------- render

#
# One render job (the row the user, quotas and SSE see) fans out into one queue task per
# clip, so clips of the same render can be cut on different workers at once. At most
# `render_parallel` of them are in flight: the first batch is enqueued up front and every
# clip that ends claims and enqueues the next index. Redis keeps the shared counters; the
# clip that brings `finished` to the total closes the job.
#
# While clips are in flight the job's worker_id is FANOUT and the owners hash records
# which worker holds each clip — that's what app.worker.sweep_orphans checks. The `alive`
# key is refreshed whenever a clip of the job starts or ends: a FANOUT job with no clip
# held and no recent activity lost its chain (a clip task that died before claiming its
# slot) and is failed by the sweep instead of staying 'running' forever.

FANOUT = "fanout"
_RENDER_KEY_TTL = 2 * 86400
_SRC_CACHE = Path(tempfile.gettempdir()) / "kappacut_render_src"
_SRC_CACHE_MAX_AGE = 3 * 3600
_src_locks: dict[str, asyncio.Lock] = {}


def render_key(job_id: uuid.UUID | str, part: str) -> str:
    # part: next | finished | failed | errors | owners | alive
    return f"kappacut:render:{job_id}:{part}"


async def _touch_render(redis, job_id: uuid.UUID) -> None:
    await redis.set(render_key(job_id, "alive"), 1, ex=settings.job_timeout_seconds)


async def _local_source(job_id: uuid.UUID, src_key: str) -> Path:
    """The source on this worker's disk, downloaded once per job and shared by every clip of
    it this worker cuts (one download per clip used to move the file N times)."""
    d = _SRC_CACHE / str(job_id)
    dest = d / Path(src_key).name
    async with _src_locks.setdefault(str(job_id), asyncio.Lock()):
        if not dest.exists():
            part = dest.with_name(dest.name + ".part")
            await asyncio.to_thread(storage.download, src_key, part)
            part.replace(dest)
    os.utime(d)                                   # in use: keep it out of the age prune
    return dest


def _prune_source_cache(keep: uuid.UUID | None = None) -> None:
    """Drop cached sources of renders that ended (on this worker or another one)."""
    if not _SRC_CACHE.exists():
        return
    now = time.time()
    for d in _SRC_CACHE.iterdir():
        if d.name != str(keep) and now - d.stat().st_mtime > _SRC_CACHE_MAX_AGE:
            shutil.rmtree(d, ignore_errors=True)
            _src_locks.pop(d.name, None)


# Owners-hash values are "<worker id>|<task token>". The claim and the release are each one
# Lua call, so a stale or duplicate copy of a clip task can neither overwrite the owner a
# live retry recorded nor delete it and end the clip with nothing uploaded.
_CLAIM_CLIP = """
if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then return 0 end
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then return -1 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""
_RELEASE_CLIP = """
if redis.call('HGET', KEYS[1], ARGV[1]) == ARGV[2] then
  return redis.call('HDEL', KEYS[1], ARGV[1])
end
return 0
"""


def owner_worker(owner: str) -> str:
    """The worker id in an owners-hash value (entries from before task tokens are bare)."""
    return owner.split("|", 1)[0]


async def release_clip(redis, job_id: uuid.UUID | str, idx: int, owner: str) -> bool:
    """Remove `owner`'s hold on clip `idx` if it still has it. True = the caller now owns
    the clip's ending (counting it, or retrying it) and nobody else will."""
    return bool(await redis.eval(_RELEASE_CLIP, 1, render_key(job_id, "owners"), str(idx), owner))


# Bump whenever cut_clip / render_one produce different bytes from the same inputs, so
# clips stored by the old code are cut again rather than reused.
RENDER_FP_VERSION = 1


def render_cfg() -> dict:
    return {**pipeline.get_cfg(), "max_fps": settings.render_max_fps}


def clip_fingerprint(src_key: str, src_size: int, r: dict, segments: list[dict], cfg: dict) -> str:
    """Everything that decides a rendered clip's bytes. The label only names the file, so a
    renamed or reordered clip still matches. Captions without explicit lines are built from
    the transcript, so the segments under the range count too. A user sfx re-uploaded under
    the same name isn't seen; re-adding it to the clip changes nothing either."""
    start, end = float(r.get("start") or 0), float(r.get("end") or 0)
    cap = r.get("captions") or {}
    under = [] if not cap or cap.get("lines") else [
        s for s in segments if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end]
    body = {"v": RENDER_FP_VERSION, "src": [src_key, int(src_size)],
            "range": {k: v for k, v in r.items() if k != "label"}, "segments": under,
            "fps": int(cfg.get("max_fps") or 0), "loudnorm": bool(cfg.get("loudnorm", True))}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:32]


async def _stored_output(project_id: uuid.UUID, fp: str) -> dict | None:
    async with async_session_maker() as s:
        row = (await s.execute(
            select(RenderOutput).where(RenderOutput.project_id == project_id,
                                       RenderOutput.fingerprint == fp)
            .order_by(RenderOutput.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if row is None:
            return None
        return {"filename": row.filename, "duration": row.duration, "vertical": row.vertical,
                "captions": row.captions, "sfx_count": row.sfx_count, "bytes": row.bytes}


async def count_to_render(s, project: Project, ranges: list[dict]) -> int:
    """How many of `ranges` a render would actually cut - the render quota counts only
    those. The rest match a stored clip and are reused (see run_render_clip)."""
    have = set((await s.execute(
        select(RenderOutput.fingerprint).where(RenderOutput.project_id == project.id,
                                               RenderOutput.fingerprint.is_not(None))
    )).scalars())
    if not have:
        return len(ranges)
    src_key = await asyncio.to_thread(storage.find_source, project.user_id, project.id)
    if src_key is None:
        return len(ranges)
    size = await asyncio.to_thread(storage.size_of, src_key)
    segments: list[dict] = []
    if any((r.get("captions") or {}) and not (r.get("captions") or {}).get("lines") for r in ranges):
        analysis = (await s.execute(
            select(Analysis).where(Analysis.project_id == project.id))).scalar_one_or_none()
        segments = analysis.segments if analysis else []
    cfg = render_cfg()
    return sum(clip_fingerprint(src_key, size, r, segments, cfg) not in have for r in ranges)


async def run_render(job_id: uuid.UUID) -> None:
    async with async_session_maker() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        if job.status != "queued":    # cancelled while waiting (e.g. for a retry): stay ended
            return
        project = await s.get(Project, job.project_id)
        if project is None:           # deleted while the job sat in the queue
            await _fail(job_id, "project no longer exists")
            return
        user_id, project_id = project.user_id, project.id
        payload = dict(job.payload or {})
        total = len(payload.get("ranges") or [])
        if not await _claim(s, job_id):
            return
        await s.commit()

    attempt = 0
    try:
        attempt = await _begin_attempt(job_id)
        src_key = await asyncio.to_thread(storage.find_source, user_id, project_id)
        if src_key is None or not total:
            await _fail(job_id, "nothing to render")
            return
        # What every clip needs to know about the source, found once here: each clip task
        # used to LIST the bucket, HEAD the source and ffprobe it twice on its own.
        src = {"key": src_key, "size": await asyncio.to_thread(storage.size_of, src_key)}
        try:
            src.update(await _in_thread(f"probe:{job_id}", cf.probe_video,
                                        storage.presign_internal(src_key, ttl=300)))
        except Exception:  # noqa: BLE001 — the clips fall back to probing for themselves
            log.warning("render %s: couldn't probe the source", job_id, exc_info=True)
        await _set_job(job_id, payload={**payload, "src": src})
        redis = await get_pool()
        width = max(1, min(settings.render_parallel, total))
        # a retried start whose clips were already queued must not queue (and render) them twice
        if await redis.set(render_key(job_id, "next"), width, ex=_RENDER_KEY_TTL, nx=True):
            for part in ("finished", "failed"):
                await redis.set(render_key(job_id, part), 0, ex=_RENDER_KEY_TTL)
            await _touch_render(redis, job_id)
            for i in range(width):
                await redis.enqueue_job("render_clip_task", str(job_id), i)
        # Hand ownership to the clips only once they're queued: dying before this leaves
        # the job on our worker id, which the sweep then requeues like any orphan.
        await _set_job(job_id, worker_id=FANOUT,
                       stage=f"Rendering {total} clip{'s' if total != 1 else ''}")
        log.info("render %s: %d clip(s), %d at a time", project_id, total, width)
    except Exception as e:  # noqa: BLE001
        log.exception("render %s failed to start (attempt %d)", project_id, attempt)
        await _retry_or_fail(job_id, "render", e)


async def run_render_clip(job_id: uuid.UUID, idx: int) -> None:
    started = time.monotonic()
    scope = f"render:{job_id}:{idx}"
    # Ownership is per task, not per worker: two copies of one clip (a resume and the
    # original, or two sweeps) can land on the same worker, and with only the worker id
    # each took the other's claim for its own.
    me = f"{WORKER_ID or ''}|{uuid.uuid4().hex[:12]}"
    redis = await get_pool()
    owners = render_key(job_id, "owners")
    try:
        async with async_session_maker() as s:
            job = await s.get(Job, job_id)
            if job is not None and job.status == "queued":
                # its start is being retried right now: come back once it's running again
                await redis.enqueue_job("render_clip_task", str(job_id), idx, _defer_by=10)
                return
            # cancelled, failed or swept while this sat in the queue: stop the chain here
            if job is None or job.status != "running":
                return
            project = await s.get(Project, job.project_id)
            if project is None:
                await _fail(job_id, "project no longer exists")
                return
            user_id, project_id = project.user_id, project.id
            payload = dict(job.payload or {})
            ranges = list(payload.get("ranges") or [])
            total = len(ranges)
            r = ranges[idx]
            src_info = dict(payload.get("src") or {})
            segments: list[dict] = []
            if (r.get("captions") or {}) and not (r.get("captions") or {}).get("lines"):
                analysis = (await s.execute(
                    select(Analysis).where(Analysis.project_id == project_id))).scalar_one_or_none()
                segments = analysis.segments if analysis else []
        # 0: the clip already ended (a resumed or doubly swept copy); -1: another task holds
        # it, and the sweep re-runs it if that task's worker dies. Either way, not ours.
        if await redis.eval(_CLAIM_CLIP, 2, owners, render_key(job_id, "ended"),
                            str(idx), me, _RENDER_KEY_TTL) != 1:
            log.info("render %s clip %d: taken or already done - dropping this copy", job_id, idx + 1)
            return
        await _touch_render(redis, job_id)
    except Exception as e:  # noqa: BLE001
        if not retriable(e):
            raise
        # nothing is held yet, so nothing would pick the clip up before the hour-long
        # `alive` timeout: put this task back instead
        log.warning("render %s clip %d: couldn't start (%s) - retrying", job_id, idx + 1, e)
        with contextlib.suppress(Exception):
            await redis.enqueue_job("render_clip_task", str(job_id), idx, _defer_by=CLIP_RETRY_DELAY)
        return

    err: str | None = None
    # The temp dir goes away in `finally`, so a crashed clip can't leave a source behind.
    tmp = tempfile.TemporaryDirectory(prefix="kappacut_render_")
    try:
        await asyncio.to_thread(_prune_source_cache, job_id)
        await _set_job(job_id, stage=f"Rendering clip {idx + 1} of {total}")
        if src_info.get("key"):
            src_key, src_size = src_info["key"], int(src_info.get("size") or 0)
        else:                                     # a render started before run_render recorded it
            src_key = await asyncio.to_thread(storage.find_source, user_id, project_id)
            if src_key is None:
                raise RuntimeError("source file missing")
            src_size = await asyncio.to_thread(storage.size_of, src_key)
        cfg = render_cfg()
        fp = clip_fingerprint(src_key, src_size, r, segments, cfg)
        name = pipeline.clip_filename(r, idx + 1)
        hit = await _stored_output(project_id, fp)
        if hit and not await asyncio.to_thread(
                storage.exists, storage.clip_key(user_id, project_id, hit["filename"])):
            hit = None

        if hit:
            # Same range, settings and source as a clip already stored: that file is this
            # clip. Under a new name (renamed or reordered) it's a server-side copy; nothing
            # is cut, and nothing counts against the render quota.
            if hit["filename"] != name:
                await asyncio.to_thread(storage.copy, storage.clip_key(user_id, project_id, hit["filename"]),
                                        storage.clip_key(user_id, project_id, name))
            rec = {"file": name, "duration": hit["duration"], "vertical": hit["vertical"],
                   "captions": hit["captions"], "sfx": hit["sfx_count"]}
            size = hit["bytes"]
        else:
            root = Path(tmp.name)
            if src_size <= settings.render_local_source_max_bytes:
                src = await _local_source(job_id, src_key)
            else:
                src = storage.presign_internal(src_key)

            # stage this clip's sfx objects (builtin or the user's) for the renderer
            sfx_stage = root / "sfx"
            sfx_stage.mkdir(parents=True, exist_ok=True)
            for fn in _stageable_sfx([r]):
                for key in (storage.builtin_sfx_key(fn), storage.user_sfx_key(user_id, fn)):
                    if await asyncio.to_thread(storage.exists, key):
                        await asyncio.to_thread(storage.download, key, sfx_stage / fn)
                        break

            out_dir = root / "clips"
            rec = await _in_thread(scope, pipeline.render_one, src, r, idx + 1, cfg,
                                   out_dir, segments, sfx_stage, None, src_info or None)
        # cancelled (or swept) while cutting: don't upload, store or bill the clip
        async with async_session_maker() as s:
            live = await s.scalar(select(Job.status).where(Job.id == job_id)) == "running"
        # still ours? if the sweep took the clip away (heartbeat lapsed) a retry owns it now
        held_by = await redis.hget(owners, str(idx))
        mine = (held_by.decode() if isinstance(held_by, bytes) else held_by) == me
        if rec is not None and live and mine:
            key = storage.clip_key(user_id, project_id, rec["file"])
            if not hit:
                local = out_dir / rec["file"]
                size = await asyncio.to_thread(storage.put_file, key, local, "video/mp4") \
                    if local.exists() else 0
            async with async_session_maker() as s:
                # the progress bump doubles as the liveness gate for the rows below
                bumped = (await s.execute(update(Job).where(Job.id == job_id, Job.status.in_(_LIVE))
                                          .values(progress_pct=Job.progress_pct + 1))).rowcount
                if bumped:
                    # same name = same object, just overwritten: replace its row, don't add one
                    await s.execute(delete(RenderOutput).where(
                        RenderOutput.project_id == project_id, RenderOutput.filename == rec["file"]))
                    s.add(RenderOutput(
                        project_id=project_id, job_id=job_id,
                        filename=rec["file"], duration=float(rec.get("duration") or 0.0),
                        vertical=bool(rec.get("vertical")), captions=bool(rec.get("captions")),
                        sfx_count=int(rec.get("sfx") or 0),
                        bytes=size, fingerprint=fp,
                    ))
                    if not hit:
                        s.add(UsageEvent(user_id=user_id, project_id=project_id, kind="render_clip", quantity=1))
                await s.commit()
            # Ended between the check and the upload: drop what this task wrote - unless a
            # row still lists that name. Then it's an earlier render's clip, overwritten in
            # place, and deleting it would leave that row pointing at nothing.
            if not bumped and not (hit and hit["filename"] == rec["file"]):
                async with async_session_maker() as s:
                    listed = await s.scalar(select(RenderOutput.id).where(
                        RenderOutput.project_id == project_id,
                        RenderOutput.filename == rec["file"]).limit(1))
                if not listed:
                    await asyncio.to_thread(storage.delete_key, key)
    except asyncio.CancelledError:
        cf.cancel_scope(scope)
        if _timed_out(started):
            # out of time once is out of time again: count the clip failed, don't retry it
            async def end() -> None:
                if await release_clip(redis, job_id, idx, me):
                    await clip_ended(job_id, total, idx, f"clip {idx + 1}: {_timeout_msg()}")

            await asyncio.shield(end())
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("render %s clip %d failed", project_id, idx + 1)
        # kappacut.run already redacts what ffmpeg said; this covers every other exception
        err = f"clip {idx + 1}: {type(e).__name__}: {cf.redact_urls(str(e))}"
    finally:
        tmp.cleanup()

    # Releasing our own hold is the claim on this clip's ending: if the sweep took it away
    # (our heartbeat lapsed while we kept working) it was counted or re-run already and must
    # not be counted again - and whatever task holds it now keeps its hold.
    # A crash between the release and clip_ended breaks the chain; `alive` catches it.
    if await release_clip(redis, job_id, idx, me):
        if err:
            await retry_or_end_clip(job_id, total, idx, err)
        else:
            await clip_ended(job_id, total, idx, None)


CLIP_RETRY_DELAY = 10


async def retry_or_end_clip(job_id: uuid.UUID, total: int, idx: int, err: str) -> None:
    """A clip that failed (or whose worker died, or whose task got lost) runs again, up to
    MAX_ATTEMPTS in all, before it counts as failed. Its slot stays taken meanwhile."""
    redis = await get_pool()
    tries = await redis.hincrby(render_key(job_id, "tries"), str(idx), 1)
    await redis.expire(render_key(job_id, "tries"), _RENDER_KEY_TTL)
    if tries < MAX_ATTEMPTS:
        await _touch_render(redis, job_id)
        await redis.enqueue_job("render_clip_task", str(job_id), idx, _defer_by=CLIP_RETRY_DELAY)
        log.warning("render %s clip %d: %s - retry %d/%d", job_id, idx + 1, err, tries + 1, MAX_ATTEMPTS)
    else:
        await clip_ended(job_id, total, idx, f"{err} (gave up after {tries} attempts)")


async def resume_render(job_id: uuid.UUID, total: int) -> None:
    """The chain broke where no owner was recorded (a clip task lost before it claimed its
    slot): re-queue the clips that were handed out but never ended, or the next one."""
    redis = await get_pool()
    ended = {int(x) for x in await redis.smembers(render_key(job_id, "ended"))}
    claimed = min(int(await redis.get(render_key(job_id, "next")) or 0), total)
    missing = [i for i in range(claimed) if i not in ended]
    if not missing:
        if len(ended) >= total:
            await _finish_render(job_id, total)
            return
        nxt = await redis.incr(render_key(job_id, "next")) - 1
        missing = [nxt] if nxt < total else []
    log.warning("render %s: chain broke - resuming clip(s) %s", job_id, [i + 1 for i in missing])
    for i in missing:
        await retry_or_end_clip(job_id, total, i, f"clip {i + 1}: its task was lost")


async def clip_ended(job_id: uuid.UUID, total: int, idx: int, err: str | None) -> None:
    """Account for one finished (or finally failed) clip: close the job if it was the last
    one, otherwise hand its slot to the next unclaimed clip."""
    redis = await get_pool()
    await _touch_render(redis, job_id)
    if not await redis.sadd(render_key(job_id, "ended"), str(idx)):
        return                                    # already counted (a duplicate task)
    await redis.expire(render_key(job_id, "ended"), _RENDER_KEY_TTL)
    if err:
        await redis.incr(render_key(job_id, "failed"))
        await redis.rpush(render_key(job_id, "errors"), err[:500])
        await redis.expire(render_key(job_id, "errors"), _RENDER_KEY_TTL)
    if await redis.incr(render_key(job_id, "finished")) >= total:
        await _finish_render(job_id, total)
    else:
        nxt = await redis.incr(render_key(job_id, "next")) - 1
        if nxt < total:
            await redis.enqueue_job("render_clip_task", str(job_id), nxt)


async def _finish_render(job_id: uuid.UUID, total: int) -> None:
    redis = await get_pool()
    failed = int(await redis.get(render_key(job_id, "failed")) or 0)
    errors = [e.decode() if isinstance(e, bytes) else e
              for e in await redis.lrange(render_key(job_id, "errors"), 0, 2)]
    async with async_session_maker() as s:
        rendered = int(await s.scalar(select(Job.progress_pct).where(Job.id == job_id)) or 0)
    if failed >= total:
        await _fail(job_id, errors[0] if errors else "render failed")
    else:
        await _set_job(
            job_id, status="done", stage="done", finished_at=_now(),
            result={"clips": rendered, "total": total, "failed": failed},
            # the render still finished; the editor shows this as a warning, not a failure
            error=(f"{failed} of {total} clips failed — {errors[0]}" if failed else None),
        )
    for part in ("next", "finished", "failed", "errors", "alive", "tries", "ended"):
        await redis.delete(render_key(job_id, part))
    # this worker's cached copy of the source; other workers' copies age out of the prune
    shutil.rmtree(_SRC_CACHE / str(job_id), ignore_errors=True)
    _src_locks.pop(str(job_id), None)
    log.info("render job %s done — %d clip(s), %d failed", job_id, rendered, failed)
