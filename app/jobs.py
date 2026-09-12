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
import shutil
import time
import uuid

from sqlalchemy import select, update

import pipeline
from app.db import async_session_maker
from app.models.job import Job, RenderOutput
from app.models.project import Analysis, Project
from app.models.usage import UsageEvent
from app.models.user import User
from app.queue import enqueue_render
from app.quota import check_quota
from app import storage

log = logging.getLogger("clipfinder.jobs")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _set_job(job_id: uuid.UUID, **fields) -> None:
    async with async_session_maker() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(**fields))
        await s.commit()


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
            await check_quota(s, user, "render")
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
        job.status, job.started_at = "running", _now()
        project.status = "analyzing"
        await s.commit()
        user_id, project_id, ext = project.user_id, project.id, project.source_ext
        job_taste = (job.payload or {}).get("taste")
        auto_render = project.auto_render

    src = storage.find_source(user_id, project_id)
    if src is None:
        await _set_job(job_id, status="error", error="source file missing", finished_at=_now())
        async with async_session_maker() as s:
            await s.execute(update(Project).where(Project.id == project_id).values(status="failed"))
            await s.commit()
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
    try:
        cfg = pipeline.get_cfg()          # fresh dict per call (cf.load_config builds dict(DEFAULTS))
        if job_taste:
            cfg["taste"] = job_taste
        result = await asyncio.to_thread(
            functools.partial(pipeline.analyze, src, cfg, on_progress)
        )
        q.put_nowait(None)
        await drainer

        async with async_session_maker() as s:
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
            dur = float(result.get("duration") or 0.0)
            await s.execute(update(Project).where(Project.id == project_id).values(
                status="ready", duration_seconds=dur, error=None,
            ))
            await s.execute(update(Job).where(Job.id == job_id).values(
                status="done", progress_pct=100, progress_msg="done",
                finished_at=_now(), result={"duration": dur, "clips": len(result["clips"])},
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
        await _set_job(job_id, status="error", error=f"{type(e).__name__}: {e}", finished_at=_now())
        async with async_session_maker() as s:
            await s.execute(update(Project).where(Project.id == project_id).values(
                status="failed", error=str(e)[:2000],
            ))
            await s.commit()


# --------------------------------------------------------------------- render

async def run_render(job_id: uuid.UUID) -> None:
    async with async_session_maker() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        project = await s.get(Project, job.project_id)
        job.status, job.started_at = "running", _now()
        await s.commit()
        user_id, project_id = project.user_id, project.id
        ranges = list((job.payload or {}).get("ranges") or [])

    src = storage.find_source(user_id, project_id)
    if src is None or not ranges:
        await _set_job(job_id, status="error", error="nothing to render", finished_at=_now())
        return

    async with async_session_maker() as s:
        analysis = (
            await s.execute(select(Analysis).where(Analysis.project_id == project_id))
        ).scalar_one_or_none()
        segments = analysis.segments if analysis else []

    out_dir = storage.clips_dir(user_id, project_id)

    # stage every referenced sfx file (builtin or the user's) into one dir for the renderer
    sfx_stage = storage.project_dir(user_id, project_id) / "_sfxstage"
    sfx_stage.mkdir(parents=True, exist_ok=True)
    for r in ranges:
        for s_ in (r.get("sfx") or []):
            fn = (s_ or {}).get("file")
            if not fn or (sfx_stage / fn).exists():
                continue
            for cand in (storage.builtin_sfx_dir() / fn, storage.user_sfx_dir(user_id) / fn):
                if cand.is_file():
                    shutil.copy2(cand, sfx_stage / fn)
                    break

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def on_clip(rec: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, ("clip", rec))

    def on_stage(msg: str) -> None:
        loop.call_soon_threadsafe(q.put_nowait, ("stage", msg))

    done = 0

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
                async with async_session_maker() as s:
                    s.add(RenderOutput(
                        project_id=project_id, job_id=job_id,
                        filename=val["file"], duration=float(val.get("duration") or 0.0),
                        vertical=bool(val.get("vertical")), captions=bool(val.get("captions")),
                        sfx_count=int(val.get("sfx") or 0),
                        bytes=(out_dir / val["file"]).stat().st_size if (out_dir / val["file"]).exists() else 0,
                    ))
                    s.add(UsageEvent(user_id=user_id, project_id=project_id, kind="render_clip", quantity=1))
                    await s.execute(update(Job).where(Job.id == job_id).values(progress_pct=done))
                    await s.commit()

    drainer = asyncio.create_task(drain())
    try:
        cfg = pipeline.get_cfg()
        await asyncio.to_thread(functools.partial(
            pipeline.render, src, ranges, cfg, out_dir, segments, sfx_stage,
            on_clip=on_clip, on_stage=on_stage,
        ))
        q.put_nowait(None)
        await drainer
        await _set_job(job_id, status="done", stage="done", finished_at=_now(),
                       result={"clips": done, "total": len(ranges)})
        log.info("render %s done — %d clip(s)", project_id, done)
    except Exception as e:  # noqa: BLE001
        q.put_nowait(None)
        with contextlib.suppress(Exception):
            await drainer
        log.exception("render %s failed", project_id)
        await _set_job(job_id, status="error", error=f"{type(e).__name__}: {e}", finished_at=_now())
    finally:
        shutil.rmtree(sfx_stage, ignore_errors=True)
