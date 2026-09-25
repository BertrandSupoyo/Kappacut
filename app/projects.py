"""Per-user projects, jobs, editor state, renders, and media serving."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, select
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


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _owned(project_id: uuid.UUID, user: User, session: AsyncSession) -> Project:
    p = await session.get(Project, project_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(404, "project not found")
    return p


async def _latest_job(session: AsyncSession, project_id: uuid.UUID, kind: str) -> Job | None:
    return (
        await session.execute(
            select(Job)
            .where(Job.project_id == project_id, Job.kind == kind)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _validate_ranges(raw: object) -> list[dict]:
    """Shape-check the render payload before it reaches the queue.

    The worker runs one ffmpeg process per range, minutes after the request that caused
    them has already returned 200 — so junk numbers surface as a failed job nobody can
    trace, and an over-long list as CPU nobody authorised.
    """
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "no ranges")
    if len(raw) > MAX_RANGES_PER_JOB:
        raise HTTPException(
            400, f"too many clips in one render ({len(raw)}; max {MAX_RANGES_PER_JOB})"
        )
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise HTTPException(400, f"range {i}: expected an object")
        try:
            start, end = float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, f"range {i}: start and end must be numbers")
        if not 0 <= start < end:
            raise HTTPException(400, f"range {i}: needs 0 <= start < end")
    return raw


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

    await check_quota(session, user, "project")

    p = Project(
        user_id=user.id,
        name=Path(filename).stem[:255] or "untitled",
        taste=taste,
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
    projects = (
        await session.execute(
            select(Project).where(Project.user_id == user.id).order_by(Project.created_at.desc())
        )
    ).scalars().all()

    out = []
    for p in projects:
        analysis = (
            await session.execute(select(Analysis).where(Analysis.project_id == p.id))
        ).scalar_one_or_none()
        rendered = await session.scalar(
            select(func.count()).select_from(RenderOutput).where(RenderOutput.project_id == p.id)
        )
        edit = await session.get(ProjectEdit, p.id)
        out.append({
            "id": str(p.id),
            "name": p.name,
            "status": p.status,
            "duration": p.duration_seconds or 0,
            "clips": len(analysis.clips) if analysis else 0,
            "rendered": rendered or 0,
            "has_edits": edit is not None,
            "created_at": p.created_at.isoformat(),
            "last_opened_at": p.last_opened_at.isoformat() if p.last_opened_at else None,
        })
    return {"projects": out}


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = await _owned(project_id, user, session)
    p.last_opened_at = _now()
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
    with tempfile.TemporaryDirectory(prefix="clipfinder_upload_") as tmp:
        staged = Path(tmp) / f"source{ext}"
        with staged.open("wb") as fh:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > settings.quota_max_upload_bytes:
                    raise HTTPException(413, "file exceeds the size limit")
                await run_in_threadpool(fh.write, chunk)
        await check_quota(session, user, "upload", extra_bytes=size)
        await run_in_threadpool(
            storage.put_file, storage.source_key(user.id, p.id, ext), staged
        )

    p.source_ext = ext
    p.source_bytes = size
    p.source_filename = (file.filename or p.source_filename)[:255]
    p.status = "queued"
    p.error = None
    job = Job(user_id=user.id, project_id=p.id, kind="analyze", status="queued")
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
    edit = await session.get(ProjectEdit, project_id)
    if edit:
        edit.state = state
    else:
        session.add(ProjectEdit(project_id=project_id, state=state))
    await session.commit()
    edit = await session.get(ProjectEdit, project_id)
    return {"ok": True, "updated_at": edit.updated_at.isoformat()}


# --------------------------------------------------------------- render

@router.post("/{project_id}/render")
async def start_render(
    project_id: uuid.UUID,
    body: dict,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = await _owned(project_id, user, session)
    ranges = _validate_ranges((body or {}).get("ranges"))
    await _guard_enqueue(session, user)
    await check_quota(session, user, "render", count=len(ranges))

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

    await check_credits(session, user, "thumbnail")

    try:
        img = await generate_thumbnail(_thumbnail_prompt(analysis.clips[clip_index]))
    except ProviderError as e:
        raise HTTPException(502, f"thumbnail generation failed: {e}")

    await run_in_threadpool(
        storage.put_bytes, storage.thumbnail_key(p.user_id, p.id, clip_index), img, "image/png"
    )
    session.add(UsageEvent(user_id=user.id, project_id=p.id, kind="credit_thumbnail", quantity=1))
    await session.commit()
    return {"url": f"/media/{project_id}/thumbnails/{clip_index}.png"}


@router.get("/{project_id}/render/events")
async def render_events(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _owned(project_id, user, session)

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

def _redirect(key: str) -> RedirectResponse:
    return RedirectResponse(storage.presign_get(key), status_code=302)


async def _derive(key: str, src_key: str, build: list, what: str) -> None:
    """Generate a derived asset (waveform, frame) with ffmpeg and store it.

    `build` is the ffmpeg argv with `{src}` and `{out}` placeholders — the source comes
    down to a temp dir, ffmpeg runs against local files, the result goes up, and the temp
    dir goes away. This is the only place the web process still runs ffmpeg; PLAN-v2.md
    Phase B ("editor proxy") is where it stops doing even this.
    """
    import clipfinder as cf

    def work() -> None:
        with tempfile.TemporaryDirectory(prefix="clipfinder_derive_") as tmp:
            root = Path(tmp)
            src = storage.download(src_key, root / Path(src_key).name)
            out = root / Path(key).name
            cf.run([a.format(src=str(src), out=str(out)) for a in build])
            storage.put_file(key, out)

    try:
        await run_in_threadpool(work)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"{what} failed: {e}")


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
    return _redirect(src)


@media_router.get("/{project_id}/waveform.png")
async def media_waveform(
    project_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
):
    import clipfinder as cf

    p = await _owned(project_id, user, session)
    key = storage.waveform_key(p.user_id, p.id)
    if not await run_in_threadpool(storage.exists, key):
        src = await run_in_threadpool(storage.find_source, p.user_id, p.id)
        if not src:
            raise HTTPException(404, "no source")
        await _derive(key, src, [
            cf.FFMPEG, "-y", *cf._thread_args(), "-i", "{src}", "-filter_complex",
            "aformat=channel_layouts=mono,showwavespic=s=2000x150:colors=#a99bff:scale=sqrt",
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
    import clipfinder as cf

    p = await _owned(project_id, user, session)
    # clamp to the known duration: `t` is caller-supplied and every miss costs an ffmpeg
    # run plus a stored object, so an unbounded value is an unbounded bill
    limit = int(p.duration_seconds or 0) or MAX_FRAME_SECOND
    sec = max(0, min(int(t), limit))
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
    return _redirect(key)
