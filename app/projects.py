"""Per-user projects, jobs, editor state, renders, and media serving."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import re
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.auth import current_verified_user
from app.config import settings
from app.credits import check_credits
from app.db import async_session_maker, get_session
from app.models.job import Job, RenderOutput
from app.models.project import Analysis, Project, ProjectEdit
from app.models.usage import UsageEvent
from app.models.user import User
from app.queue import enqueue_analyze, enqueue_render
from app.quota import check_quota
from app.thumbnails import ProviderError, generate_thumbnail

log = logging.getLogger("kappacut.projects")
router = APIRouter(prefix="/api/projects", tags=["projects"])
media_router = APIRouter(prefix="/media", tags=["media"])

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".m2ts", ".mts"}
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
# A render job holds one worker slot for its whole duration, so a single request must not
# be able to enqueue unbounded work. The per-day clip quota is the real limit; this is a
# structural backstop on one payload.
MAX_RANGES_PER_JOB = 100
# Ceiling on /frame.jpg?t= when the project's duration isn't known yet — one ffmpeg run
# and one stored object per distinct second, so this can't be left open.
MAX_FRAME_SECOND = 6 * 3600
# render payload bounds: what the editor can produce, plus headroom
MAX_SFX_PER_CLIP = 32
MAX_CAPTION_LINES = 400
MAX_TRACK_POINTS = 240                    # kappacut keeps at most TRACK_MAX_POINTS * 2
# the values pipeline.build_ass / kappacut.cut_clip understand
_CROP_MODES = {"crop", "fill", "cropfill"}
_CAPTION_STYLES = {"clean", "bold", "box", "pop"}
_CAPTION_POSITIONS = {"bottom", "center", "top"}
_CAPTION_ANIMS = {"none", "pop", "fade", "slide", "bounce"}
_ASPECT_RE = re.compile(r"\d{1,3}(\.\d{1,3})?[/:]\d{1,3}(\.\d{1,3})?")


# a categories.toml table name; anything unknown there falls back to [category.general]
_CONTENT_TYPE_RE = re.compile(r"[a-z]{2,32}")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def analyze_payload(p: Project) -> dict | None:
    """What an analyze job needs from its project's New-project options. One place, used by
    the upload hook, Retry and Re-analyze, so they can't drift apart."""
    out = {k: v for k, v in (("taste", p.taste), ("count", p.clip_count),
                             ("content_type", p.content_type)) if v}
    return out or None


async def clear_stale_source(user_id: uuid.UUID, project_id: uuid.UUID, source: str) -> None:
    """A replaced source leaves its old file and derived frames behind; tidy up, but never
    let that block the analysis the new upload is for. Used by both upload paths."""
    try:
        await run_in_threadpool(storage.clear_stale_media, user_id, project_id, source)
    except Exception:  # noqa: BLE001
        log.warning("couldn't clear stale media for %s", project_id, exc_info=True)


async def _owned(project_id: uuid.UUID, user: User, session: AsyncSession) -> Project:
    p = await session.get(Project, project_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(404, "project not found")
    return p


async def _release(session: AsyncSession) -> None:
    """Hand the request's DB connection back before a long-lived response.

    A `yield` dependency is closed only once the response has finished sending, so an
    event stream would otherwise hold its session - and a pooled connection - for up to an
    hour: ~15 open editors exhausted the pool and every other request timed out. The
    streams poll with their own short sessions instead.
    """
    await session.close()


async def _latest_job(session: AsyncSession, project_id: uuid.UUID, kind: str) -> Job | None:
    return (
        await session.execute(
            select(Job)
            .where(Job.project_id == project_id, Job.kind == kind)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _num(v: object, lo: float, hi: float, default: float) -> float:
    """A finite number clamped to [lo, hi]; anything else (a string, NaN, Infinity) is `default`."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, f)) if math.isfinite(f) else default


def _text(v: object, limit: int) -> str:
    return "" if v is None else str(v)[:limit]


def _clean_lines(lines: list, length: float) -> list[dict]:
    out = []
    for ln in lines[:MAX_CAPTION_LINES]:
        if not isinstance(ln, dict):
            continue
        words = ln.get("words") if isinstance(ln.get("words"), list) else []
        out.append({
            "start": _num(ln.get("start"), 0, length, 0), "end": _num(ln.get("end"), 0, length, 0),
            "text": _text(ln.get("text"), 500),
            "words": [{"w": _text(w.get("w"), 64), "start": _num(w.get("start"), 0, length, 0),
                       "end": _num(w.get("end"), 0, length, 0)}
                      for w in words[:200] if isinstance(w, dict)],
        })
    return out


def _clean_range(i: int, r: object) -> dict:
    """One range, rebuilt from the fields the renderer reads, every number clamped to the
    range the editor offers. The raw dict used to go to the worker as sent: a caption size
    of 100 (the slider stops at 1.6) made libass ~50x slower, and a clip could span the
    whole video."""
    if not isinstance(r, dict):
        raise HTTPException(400, f"range {i}: expected an object")
    try:
        start, end = float(r["start"]), float(r["end"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, f"range {i}: start and end must be numbers")
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise HTTPException(400, f"range {i}: needs 0 <= start < end")
    length = end - start
    if length > settings.render_max_clip_seconds:
        raise HTTPException(400, f"clip {i + 1} is {length / 60:.1f} min long - one clip can be "
                                 f"at most {settings.render_max_clip_seconds / 60:g} min")
    vertical = bool(r.get("vertical"))
    out: dict = {"start": start, "end": end, "vertical": vertical,
                 "label": _text(r.get("label") or "clip", 200)}

    crop = r.get("crop")
    out["crop"] = None
    if vertical and isinstance(crop, dict):
        c = {"mode": crop.get("mode") if crop.get("mode") in _CROP_MODES else "crop",
             "px": _num(crop.get("px"), 0, 1, 0.5), "py": _num(crop.get("py"), 0, 1, 0.5),
             "zoom": _num(crop.get("zoom"), 1, 4, 1), "blur": _num(crop.get("blur"), 2, 80, 24)}
        if isinstance(crop.get("aspect"), str) and _ASPECT_RE.fullmatch(crop["aspect"]):
            c["aspect"] = crop["aspect"]
        if isinstance(crop.get("track"), list):
            track = [[_num(p[0], 0, 1e7, 0), _num(p[1], 0, 1, 0.5), _num(p[2], 0, 1, 0.5)]
                     for p in crop["track"][:MAX_TRACK_POINTS]
                     if isinstance(p, (list, tuple)) and len(p) >= 3]
            if track:
                c["track"] = track
        out["crop"] = c

    cap = r.get("captions")
    out["captions"] = None
    if isinstance(cap, dict) and cap:
        k: dict = {
            "style": cap.get("style") if cap.get("style") in _CAPTION_STYLES else "clean",
            "position": cap.get("position") if cap.get("position") in _CAPTION_POSITIONS else "bottom",
            "size": _num(cap.get("size"), 0.5, 2.5, 1.0),
            "anim": cap.get("anim") if cap.get("anim") in _CAPTION_ANIMS else "none",
            "karaoke": bool(cap.get("karaoke")),
        }
        pos = cap.get("pos")
        if isinstance(pos, dict) and pos.get("x") is not None and pos.get("y") is not None:
            k["pos"] = {"x": _num(pos["x"], 0, 1, 0.5), "y": _num(pos["y"], 0, 1, 0.5)}
        if isinstance(cap.get("lines"), list) and cap["lines"]:
            k["lines"] = _clean_lines(cap["lines"], length)
        out["captions"] = k

    color = r.get("color")
    out["color"] = ({"preset": _text(color.get("preset") or "none", 32),
                     "b": _num(color.get("b"), -0.4, 0.4, 0), "c": _num(color.get("c"), 0.5, 1.8, 1),
                     "s": _num(color.get("s"), 0, 2, 1)}
                    if isinstance(color, dict) else None)

    sfx = r.get("sfx") if isinstance(r.get("sfx"), list) else []
    out["sfx"] = [{"file": s["file"][:255], "at": _num(s.get("at"), 0, length, 0),
                   "gain": _num(s.get("gain"), -60, 12, 0)}
                  for s in sfx[:MAX_SFX_PER_CLIP]
                  if isinstance(s, dict) and isinstance(s.get("file"), str) and s["file"]]
    return out


def _validate_ranges(raw: object) -> list[dict]:
    """Shape-check the render payload before it reaches the queue, and rebuild it.

    The worker runs one ffmpeg process per range, minutes after the request that caused
    them has already returned 200 — so junk numbers surface as a failed job nobody can
    trace, and an over-long list or clip as CPU nobody authorised.
    """
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "no ranges")
    if len(raw) > MAX_RANGES_PER_JOB:
        raise HTTPException(
            400, f"too many clips in one render ({len(raw)}; max {MAX_RANGES_PER_JOB})"
        )
    return [_clean_range(i, r) for i, r in enumerate(raw)]


async def _guard_enqueue(session: AsyncSession, user: User) -> None:
    """One active job per user; global backpressure on the analyze queue."""
    mine = await session.scalar(
        select(func.count()).select_from(Job)
        .where(Job.user_id == user.id, Job.status.in_(("queued", "running")))
    )
    if mine >= settings.quota_concurrent_jobs:
        raise HTTPException(429, "you already have a job running — wait for it to finish")
    depth = await session.scalar(
        select(func.count()).select_from(Job)
        .where(Job.kind == "analyze", Job.status.in_(("queued", "running")))
    )
    if depth >= settings.max_global_queue:
        raise HTTPException(503, "the analysis queue is full right now — try again shortly")


# --------------------------------------------------------------- create / list

@router.post("")
async def create_project(
    body: dict,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    filename = str((body or {}).get("filename") or "").strip() or "video.mp4"
    ext = Path(filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"unsupported video type ({ext or 'none'})")
    taste = str((body or {}).get("taste") or "").strip()[:2000] or None
    auto_render = bool((body or {}).get("auto_render"))
    clip_count = (body or {}).get("clip_count")
    if clip_count is not None:
        try:
            clip_count = int(clip_count)
        except (TypeError, ValueError):
            raise HTTPException(400, "clip_count must be a whole number")
        if not 1 <= clip_count <= 20:
            raise HTTPException(400, "clip_count must be between 1 and 20")
    content_type = str((body or {}).get("content_type") or "").strip().lower() or None
    if content_type and not _CONTENT_TYPE_RE.fullmatch(content_type):
        raise HTTPException(400, "unknown content type")

    await check_quota(session, user, "project")

    p = Project(
        user_id=user.id,
        name=Path(filename).stem[:255] or "untitled",
        taste=taste,
        clip_count=clip_count,
        content_type=content_type,
        auto_render=auto_render,
        source_filename=filename[:255],
        source_ext=ext,
        status="uploading",
    )
    session.add(p)
    await session.commit()
    return {"id": str(p.id), "upload_url": f"/api/projects/{p.id}/source"}


@router.get("")
async def list_projects(
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # One query. It was three per project, and each loaded the whole analysis - transcript
    # included - only to count its clips.
    clips = (select(func.jsonb_array_length(Analysis.clips))
             .where(Analysis.project_id == Project.id).scalar_subquery())
    rendered = (select(func.count()).select_from(RenderOutput)
                .where(RenderOutput.project_id == Project.id).scalar_subquery())
    has_edits = select(ProjectEdit.project_id).where(ProjectEdit.project_id == Project.id).exists()
    rows = (await session.execute(
        select(Project, clips, rendered, has_edits)
        .where(Project.user_id == user.id).order_by(Project.created_at.desc())
    )).all()

    return {"projects": [{
        "id": str(p.id),
        "name": p.name,
        "status": p.status,
        "duration": p.duration_seconds or 0,
        "clips": n_clips or 0,
        "rendered": n_rendered or 0,
        "has_edits": bool(edited),
        "created_at": p.created_at.isoformat(),
        "last_opened_at": p.last_opened_at.isoformat() if p.last_opened_at else None,
    } for p, n_clips, n_rendered, edited in rows]}


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = await _owned(project_id, user, session)
    p.last_opened_at = _now()
    # opened = no longer idle: the next idle stretch earns a fresh warning (retention.py
    # only warns projects that were never warned, so a reopened one went without it)
    p.retention_warned_at = None
    await session.commit()
    # `p` was loaded before this commit and (expire_on_commit=False) won't auto-refresh;
    # a worker can flip status/job in the gap between this request's queries, so re-read
    # the fields the response reports right before building it, as close together as
    # possible to `job` below — avoids serving a stale project.status next to a fresh job.
    await session.refresh(p, attribute_names=["status", "duration_seconds", "error"])

    job = await _latest_job(session, p.id, "analyze")
    analysis = (
        await session.execute(select(Analysis).where(Analysis.project_id == p.id))
    ).scalar_one_or_none()
    return {
        "id": str(p.id),
        "name": p.name,
        "status": p.status,
        "duration": p.duration_seconds or 0,
        "error": p.error,
        "job": None if job is None else {
            "status": job.status, "pct": job.progress_pct,
            "msg": job.progress_msg, "error": job.error,
            "requested": (job.result or {}).get("requested"),
        },
        "analysis": None if analysis is None else {
            "segments": analysis.segments, "clips": analysis.clips,
        },
    }


@router.delete("/{project_id}")
async def delete_project(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = await _owned(project_id, user, session)
    await run_in_threadpool(storage.delete_project_media, user.id, p.id)
    await session.delete(p)  # cascades analyses / edits / jobs / outputs
    await session.commit()
    return {"ok": True}


# --------------------------------------------------------------- upload + analyze

@router.post("/{project_id}/source")
async def upload_source(
    project_id: uuid.UUID,
    file: UploadFile,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = await _owned(project_id, user, session)
    await _guard_enqueue(session, user)
    await check_quota(session, user, "analyze")
    ext = Path(file.filename or p.source_filename).suffix.lower() or p.source_ext or ".mp4"
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"unsupported video type ({ext})")

    # Spool to a temp file rather than memory (this accepts up to quota_max_upload_bytes)
    # and only upload once the whole thing is here and within quota — a rejected upload
    # never creates an object. The write itself goes through the threadpool so a 2 GB
    # upload can't stall the event loop for every other request.
    size = 0
    with tempfile.TemporaryDirectory(prefix="kappacut_upload_") as tmp:
        staged = Path(tmp) / f"source{ext}"
        with staged.open("wb") as fh:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > settings.quota_max_upload_bytes:
                    raise HTTPException(413, "file exceeds the size limit")
                await run_in_threadpool(fh.write, chunk)
        await check_quota(session, user, "upload", extra_bytes=size)
        source = storage.source_key(user.id, p.id, ext)
        await run_in_threadpool(storage.put_file, source, staged)
    await clear_stale_source(user.id, p.id, source)

    p.source_ext = ext
    p.source_bytes = size
    p.source_filename = (file.filename or p.source_filename)[:255]
    p.status = "queued"
    p.error = None
    job = Job(user_id=user.id, project_id=p.id, kind="analyze", status="queued",
              payload=analyze_payload(p))       # this path used to drop the options entirely
    session.add(job)
    session.add(UsageEvent(user_id=user.id, project_id=p.id, kind="upload_bytes", quantity=size))
    await session.commit()

    job.arq_job_id = await enqueue_analyze(job.id)
    await session.commit()
    return {"job_id": str(job.id)}


@router.get("/{project_id}/analyze/events")
async def analyze_events(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _owned(project_id, user, session)
    await _release(session)

    async def gen():
        last = None
        for _ in range(7200):
            async with async_session_maker() as s:
                job = await _latest_job(s, project_id, "analyze")
            payload = {
                "status": job.status if job else None,
                "pct": job.progress_pct if job else 0,
                "msg": job.progress_msg if job else "",
                "error": job.error if job else None,
            }
            if payload != last:
                yield f"data: {json.dumps(payload)}\n\n"
                last = payload
            if not job or job.status in ("done", "error"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/{project_id}/analyze")
async def retry_analyze(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run analysis on the already-uploaded source: retry a failed project, or re-analyze
    a finished one with the current clip-picking engine.

    Same gates as the upload path; no usage is recorded here — transcription minutes are
    only charged when an analysis succeeds. A successful re-run replaces the clips and
    clears the saved edits (they're keyed by clip index); a failed one leaves both alone.
    """
    p = await _owned(project_id, user, session)
    if p.status not in ("failed", "ready"):
        raise HTTPException(409, f"this project is {p.status} — wait for it to finish")
    if await run_in_threadpool(storage.find_source, user.id, p.id) is None:
        raise HTTPException(409, "the source video is gone — re-upload it")
    await _guard_enqueue(session, user)
    await check_quota(session, user, "analyze")

    p.status = "queued"
    p.error = None
    job = Job(user_id=user.id, project_id=p.id, kind="analyze", status="queued",
              payload=analyze_payload(p))
    session.add(job)
    await session.commit()
    job.arq_job_id = await enqueue_analyze(job.id)
    await session.commit()
    return {"job_id": str(job.id)}


# --------------------------------------------------------------- editor state

@router.get("/{project_id}/edit")
async def get_edit(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _owned(project_id, user, session)
    edit = await session.get(ProjectEdit, project_id)
    return {"state": edit.state if edit else None,
            "updated_at": edit.updated_at.isoformat() if edit else None}


@router.post("/{project_id}/edit")
async def save_edit(
    project_id: uuid.UUID,
    body: dict,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _owned(project_id, user, session)
    state = (body or {}).get("state")
    if not isinstance(state, dict):
        raise HTTPException(400, "bad state")
    if len(json.dumps(state)) > settings.edit_state_max_bytes:
        raise HTTPException(413, "editor state too large")
    edit = await session.get(ProjectEdit, project_id)
    if edit:
        edit.state = state
    else:
        session.add(ProjectEdit(project_id=project_id, state=state))
    await session.commit()
    # updated_at is set by the database, so the commit leaves it expired; session.get()
    # hands back the same identity-mapped object, and touching the attribute would
    # lazy-load synchronously — MissingGreenlet, a 500 on a save that had already
    # committed, and "Save failed" in the editor on every edit. Reload it explicitly.
    edit = await session.get(ProjectEdit, project_id)
    await session.refresh(edit, attribute_names=["updated_at"])
    return {"ok": True, "updated_at": edit.updated_at.isoformat()}


# --------------------------------------------------------------- render

@router.post("/{project_id}/render")
async def start_render(
    project_id: uuid.UUID,
    body: dict,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from app.jobs import count_to_render  # local import: jobs pulls in the whole pipeline

    p = await _owned(project_id, user, session)
    ranges = _validate_ranges((body or {}).get("ranges"))
    await _guard_enqueue(session, user)
    # clips that match a stored render are reused, not cut, and don't count
    await check_quota(session, user, "render", count=await count_to_render(session, p, ranges))

    job = Job(user_id=user.id, project_id=p.id, kind="render", status="queued",
              payload={"ranges": ranges})
    session.add(job)
    await session.commit()

    job.arq_job_id = await enqueue_render(job.id)
    await session.commit()
    return {"job_id": str(job.id), "total": len(ranges)}


# --------------------------------------------------------------- AI thumbnails (Phase-boost F)

def _thumbnail_prompt(clip: dict) -> str:
    title = clip.get("title") or clip.get("hook") or "a viral short-form video moment"
    beat = clip.get("beat_type") or "moment"
    return (
        f"A vibrant, high-contrast vertical (9:16) thumbnail for a short-form video clip. "
        f"Moment: \"{title}\". Mood: {beat}. Eye-catching, bold, no text or captions in the image."
    )


@router.post("/{project_id}/clips/{clip_index}/thumbnail")
async def generate_clip_thumbnail(
    project_id: uuid.UUID,
    clip_index: int,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = await _owned(project_id, user, session)
    analysis = (
        await session.execute(select(Analysis).where(Analysis.project_id == project_id))
    ).scalar_one_or_none()
    if not analysis or not (0 <= clip_index < len(analysis.clips)):
        raise HTTPException(404, "no such clip")

    prompt = _thumbnail_prompt(analysis.clips[clip_index])

    # Book the credit before the paid call, under a per-user lock. Checking first and
    # recording after let parallel requests all pass the check before any was counted.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                          {"k": f"credits:{user.id}"})
    await check_credits(session, user, "thumbnail")
    hold = UsageEvent(user_id=user.id, project_id=p.id, kind="credit_thumbnail", quantity=1)
    session.add(hold)
    await session.commit()                      # also releases the lock

    try:
        img = await generate_thumbnail(prompt)
    except Exception as e:
        await session.delete(hold)              # nothing was bought: give the credit back
        await session.commit()
        if isinstance(e, ProviderError):
            raise HTTPException(502, f"thumbnail generation failed: {e}")
        raise

    await run_in_threadpool(
        storage.put_bytes, storage.thumbnail_key(p.user_id, p.id, clip_index), img, "image/png"
    )
    return {"url": f"/media/{project_id}/thumbnails/{clip_index}.png"}


@router.get("/{project_id}/render/events")
async def render_events(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _owned(project_id, user, session)
    await _release(session)

    async def gen():
        last = None
        for _ in range(7200):
            async with async_session_maker() as s:
                job = await _latest_job(s, project_id, "render")
                outs = []
                if job:
                    rows = (await s.execute(
                        select(RenderOutput)
                        .where(RenderOutput.job_id == job.id)
                        .order_by(RenderOutput.created_at)
                    )).scalars().all()
                    outs = [{
                        "file": r.filename,
                        "url": f"/media/{project_id}/clips/{r.filename}",
                        "duration": r.duration, "vertical": r.vertical,
                    } for r in rows]
            total = len((job.payload or {}).get("ranges") or []) if job else 0
            payload = {
                "status": job.status if job else None,
                "done": len(outs), "total": total,
                "current": job.stage if job else "",
                "error": job.error if job else None,
                "clips": outs,
            }
            if payload != last:
                yield f"data: {json.dumps(payload)}\n\n"
                last = payload
            if not job or job.status in ("done", "error"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/{project_id}/outputs")
async def list_outputs(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _owned(project_id, user, session)
    rows = (await session.execute(
        select(RenderOutput).where(RenderOutput.project_id == project_id)
        .order_by(RenderOutput.created_at.desc())
    )).scalars().all()
    return {"clips": [{
        "file": r.filename,
        "url": f"/media/{project_id}/clips/{r.filename}",
        "duration": r.duration, "vertical": r.vertical,
        "captions": r.captions, "sfx": r.sfx_count,
    } for r in rows]}


# --------------------------------------------------------------- media serving
#
# Every route here does the same two things in the same order: settle ownership against
# the database, and only then sign a short-lived URL and redirect. The bytes never pass
# through this process — `_owned()` is the entire access control, so it must run first
# and a signed URL must never be built from anything the caller supplied.

def _redirect(key: str, *, filename: str | None = None, video: bool = False) -> RedirectResponse:
    # A <video> keeps using the URL it was redirected to for every later range request, so
    # a video link signed for 5 minutes broke seeking mid-edit; videos get the longer TTL.
    ttl = settings.presign_video_ttl_seconds if video else None
    return RedirectResponse(storage.presign_get(key, ttl=ttl, filename=filename), status_code=302)


async def _derive(key: str, src_key: str, build: list, what: str) -> None:
    """Generate a derived asset (waveform, frame) with ffmpeg and store it.

    `build` is the ffmpeg argv with `{src}` and `{out}` placeholders. ffmpeg reads the
    source straight from the bucket through a signed internal URL - with `-ss` before `-i`
    one frame costs a few range requests. It used to download the whole source first: up
    to 2 GB through this process's disk per frame, and the review grid asks for several at
    once. Only the small output touches a temp dir. This is the only place the web process
    still runs ffmpeg; PLAN-v2.md Phase B ("editor proxy") is where it stops doing even this.
    """
    import kappacut as cf

    def work() -> None:
        with tempfile.TemporaryDirectory(prefix="kappacut_derive_") as tmp:
            out = Path(tmp) / Path(key).name
            src = storage.presign_internal(src_key, ttl=600)
            cf.run([a.format(src=src, out=str(out)) for a in build])
            storage.put_file(key, out)

    try:
        await run_in_threadpool(work)
    except Exception:  # noqa: BLE001
        # logged, not returned: ffmpeg's error text means nothing to the person looking at it
        log.exception("%s failed for %s", what, src_key)
        raise HTTPException(500, f"couldn't make the {what}")


@media_router.get("/{project_id}/source")
async def media_source(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
):
    p = await _owned(project_id, user, session)
    src = await run_in_threadpool(storage.find_source, p.user_id, p.id)
    if not src:
        raise HTTPException(404, "no source")
    return _redirect(src, video=True)


@media_router.get("/{project_id}/waveform.png")
async def media_waveform(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
):
    import kappacut as cf

    p = await _owned(project_id, user, session)
    key = storage.waveform_key(p.user_id, p.id)
    if not await run_in_threadpool(storage.exists, key):
        src = await run_in_threadpool(storage.find_source, p.user_id, p.id)
        if not src:
            raise HTTPException(404, "no source")
        await _derive(key, src, [
            cf.FFMPEG, "-y", *cf._thread_args(), "-i", "{src}", "-filter_complex",
            "aformat=channel_layouts=mono,showwavespic=s=2000x150:colors=#5c5c5c:scale=sqrt",
            "-frames:v", "1", "{out}",
        ], "waveform")
    return _redirect(key)


@media_router.get("/{project_id}/frame.jpg")
async def media_frame(
    project_id: uuid.UUID,
    t: float = 0.0,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
):
    import kappacut as cf

    p = await _owned(project_id, user, session)
    # clamp to the known duration: `t` is caller-supplied and every miss costs an ffmpeg
    # run plus a stored object, so an unbounded value is an unbounded bill
    limit = int(p.duration_seconds or 0) or MAX_FRAME_SECOND
    sec = max(0, min(int(t), limit)) if math.isfinite(t) else 0   # int(nan) raised: a 500
    key = storage.frame_key(p.user_id, p.id, sec)
    if not await run_in_threadpool(storage.exists, key):
        src = await run_in_threadpool(storage.find_source, p.user_id, p.id)
        if not src:
            raise HTTPException(404, "no source")
        await _derive(key, src, [
            cf.FFMPEG, "-y", *cf._thread_args(), "-ss", str(sec), "-i", "{src}",
            "-frames:v", "1", "-vf", "scale=400:-2", "-q:v", "4", "{out}",
        ], "frame")
    return _redirect(key)


@media_router.get("/{project_id}/thumbnails/{clip_index}.png")
async def media_thumbnail(
    project_id: uuid.UUID,
    clip_index: int,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
):
    p = await _owned(project_id, user, session)
    key = storage.thumbnail_key(p.user_id, p.id, clip_index)
    if not await run_in_threadpool(storage.exists, key):
        raise HTTPException(404, "no thumbnail generated yet")
    return _redirect(key)


@media_router.get("/{project_id}/clips/{name}")
async def media_clip(
    project_id: uuid.UUID,
    name: str,
    download: bool = False,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
):
    p = await _owned(project_id, user, session)
    try:
        key = storage.clip_key(p.user_id, p.id, name)
    except ValueError:
        raise HTTPException(400, "bad name")
    if not await run_in_threadpool(storage.exists, key):
        raise HTTPException(404, "no clip")
    # <a download> is ignored once the redirect leaves this origin, so the "↓" link opened
    # the video instead of saving it; the bucket's Content-Disposition makes it a download
    return _redirect(key, filename=name if download else None, video=True)
