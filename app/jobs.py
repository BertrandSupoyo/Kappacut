"""Run analyze / render off the request path, writing progress to the `jobs` row.

Called from the arq worker (app/worker.py). `pipeline.*` is blocking (ffmpeg + SDK) so it
runs in `asyncio.to_thread`; progress callbacks hop back to the loop via
`call_soon_threadsafe` onto a queue that a drain task commits to Postgres.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import functools
import logging
import re
import tempfile
import time
import uuid
from pathlib import Path

from sqlalchemy import delete, select, update

import pipeline
from app.db import async_session_maker
from app.models.job import Job, RenderOutput
from app.models.project import Analysis, Project, ProjectEdit
from app.models.usage import UsageEvent
from app.models.user import User
from app.queue import enqueue_render
from app.quota import check_quota
from app import storage

log = logging.getLogger("clipfinder.jobs")

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


def _stageable_sfx(ranges: list[dict]) -> list[str]:
    """The sfx filenames a render references, deduped — bare names only, the same rule
    clipfinder.mix_sfx enforces, so a crafted `file` can't steer the copy out of the
    staging dir (which would raise out of the staging loop and wedge the job)."""
    names: list[str] = []
    for r in ranges:
        for s_ in (r.get("sfx") or []):
            fn = (s_ or {}).get("file")
            if isinstance(fn, str) and fn and Path(fn).name == fn and fn not in names:
                names.append(fn)
    return names


def _clip_to_range(c: dict) -> dict:
    """The server-side equivalent of the frontend's keptRanges() (editor/store.ts) — built
    from a clip's `auto` suggestion (clipfinder.py:_auto_edit) since there's no ProjectEdit
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

async def run_analyze(job_id: uuid.UUID) -> None:
    async with async_session_maker() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        project = await s.get(Project, job.project_id)
        if project is None:           # deleted while the job sat in the queue
            await _fail(job_id, "project no longer exists")
            return
        job.status, job.started_at = "running", _now()
        project.status = "analyzing"
        await s.commit()
        user_id, project_id = project.user_id, project.id
        job_payload = dict(job.payload or {})
        auto_render = project.auto_render

    src_key = await asyncio.to_thread(storage.find_source, user_id, project_id)
    if src_key is None:
        await _fail(job_id, "source file missing", project_id)
        return

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def on_progress(pct: int, msg: str) -> None:
        loop.call_soon_threadsafe(q.put_nowait, (int(pct), str(msg)))

    async def drain() -> None:
        last = 0.0
        while True:
            item = await q.get()
            if item is None:
                return
            pct, msg = item
            if time.monotonic() - last > 0.4:
                await _set_job(job_id, progress_pct=pct, progress_msg=msg)
                last = time.monotonic()

    drainer = asyncio.create_task(drain())
    # ffmpeg and Whisper both need a seekable local file, so the source comes down once
    # into a temp dir this job owns and that goes away with it — including on a crash.
    tmp = tempfile.TemporaryDirectory(prefix="clipfinder_analyze_")
    try:
        src = await asyncio.to_thread(
            storage.download, src_key, Path(tmp.name) / Path(src_key).name
        )
        cfg = _apply_options(pipeline.get_cfg(), job_payload)   # fresh dict per call
        result = await asyncio.to_thread(
            functools.partial(pipeline.analyze, src, cfg, on_progress)
        )
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
    except Exception as e:  # noqa: BLE001
        q.put_nowait(None)
        with contextlib.suppress(Exception):
            await drainer
        log.exception("analyze %s failed", project_id)
        # GroqQuotaError's message is already written for the user ("try again in ~N min")
        msg = str(e) if type(e).__name__ == "GroqQuotaError" else f"{type(e).__name__}: {e}"
        await _fail(job_id, msg, project_id)
    finally:
        tmp.cleanup()


# --------------------------------------------------------------------- render

async def run_render(job_id: uuid.UUID) -> None:
    async with async_session_maker() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        project = await s.get(Project, job.project_id)
        if project is None:           # deleted while the job sat in the queue
            await _fail(job_id, "project no longer exists")
            return
        job.status, job.started_at = "running", _now()
        await s.commit()
        user_id, project_id = project.user_id, project.id
        ranges = list((job.payload or {}).get("ranges") or [])

    # Everything below runs inside the try/finally: setup used to sit outside it, so a
    # failure while staging (a bad sfx name, a full disk) escaped with the row still at
    # 'running' — an unkillable job that blocks the user's concurrency slot.
    out_dir: Path | None = None
    drainer: asyncio.Task | None = None
    q: asyncio.Queue = asyncio.Queue()
    done = 0
    # One temp dir for the whole job — source in, clips out, sfx staged. It goes away in
    # `finally`, so a crashed render cannot leave a 2 GB source behind in the container.
    tmp = tempfile.TemporaryDirectory(prefix="clipfinder_render_")

    async def drain() -> None:
        nonlocal done
        while True:
            item = await q.get()
            if item is None:
                return
            kind, val = item
            if kind == "stage":
                await _set_job(job_id, stage=str(val))
            else:
                done += 1
                # Upload as each clip lands rather than in a batch at the end: the SSE
                # already reports them one by one, so this is what makes that honest —
                # a clip the user is told about is a clip they can actually fetch.
                local = out_dir / val["file"]
                size = await asyncio.to_thread(
                    storage.put_file,
                    storage.clip_key(user_id, project_id, val["file"]), local, "video/mp4",
                ) if local.exists() else 0
                async with async_session_maker() as s:
                    s.add(RenderOutput(
                        project_id=project_id, job_id=job_id,
                        filename=val["file"], duration=float(val.get("duration") or 0.0),
                        vertical=bool(val.get("vertical")), captions=bool(val.get("captions")),
                        sfx_count=int(val.get("sfx") or 0),
                        bytes=size,
                    ))
                    s.add(UsageEvent(user_id=user_id, project_id=project_id, kind="render_clip", quantity=1))
                    await s.execute(update(Job).where(
                        Job.id == job_id, Job.status.in_(_LIVE)).values(progress_pct=done))
                    await s.commit()

    try:
        src_key = await asyncio.to_thread(storage.find_source, user_id, project_id)
        if src_key is None or not ranges:
            await _fail(job_id, "nothing to render")
            return

        async with async_session_maker() as s:
            analysis = (
                await s.execute(select(Analysis).where(Analysis.project_id == project_id))
            ).scalar_one_or_none()
            segments = analysis.segments if analysis else []

        root = Path(tmp.name)
        src = await asyncio.to_thread(storage.download, src_key, root / Path(src_key).name)
        out_dir = root / "clips"
        out_dir.mkdir(parents=True, exist_ok=True)

        # stage every referenced sfx object (builtin or the user's) into one dir for the renderer
        sfx_stage = root / "sfx"
        sfx_stage.mkdir(parents=True, exist_ok=True)
        for fn in _stageable_sfx(ranges):
            for key in (storage.builtin_sfx_key(fn), storage.user_sfx_key(user_id, fn)):
                if await asyncio.to_thread(storage.exists, key):
                    await asyncio.to_thread(storage.download, key, sfx_stage / fn)
                    break

        loop = asyncio.get_running_loop()

        def on_clip(rec: dict) -> None:
            loop.call_soon_threadsafe(q.put_nowait, ("clip", rec))

        def on_stage(msg: str) -> None:
            loop.call_soon_threadsafe(q.put_nowait, ("stage", msg))

        drainer = asyncio.create_task(drain())
        cfg = pipeline.get_cfg()
        await asyncio.to_thread(functools.partial(
            pipeline.render, src, ranges, cfg, out_dir, segments, sfx_stage,
            on_clip=on_clip, on_stage=on_stage,
        ))
        q.put_nowait(None)
        await drainer
        drainer = None
        await _set_job(job_id, status="done", stage="done", finished_at=_now(),
                       result={"clips": done, "total": len(ranges)})
        log.info("render %s done — %d clip(s)", project_id, done)
    except Exception as e:  # noqa: BLE001
        if drainer is not None:
            q.put_nowait(None)
            with contextlib.suppress(Exception):
                await drainer
        log.exception("render %s failed", project_id)
        await _fail(job_id, f"{type(e).__name__}: {e}")
    finally:
        tmp.cleanup()
