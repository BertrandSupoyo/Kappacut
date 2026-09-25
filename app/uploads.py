"""tusd resumable-upload hooks.

tusd (the `/files/*` service) POSTs here before creating an upload (`pre-create` — we
authorise + quota-check) and after it finishes (`post-finish` — we move the file into the
project dir and enqueue analysis). The client's cookie is forwarded via
`-hooks-http-forward-headers Cookie`.

Flow:  SPA `POST /api/projects` → gets an id → Uppy/tus upload to `/files/` with metadata
`{projectId, filename}` → tusd calls these hooks.

SECURITY: the body of these requests is written by tusd, but the only thing proving the
caller IS tusd is the network (the Caddyfile 404s `/api/upload/hooks` from outside, and
this endpoint is CSRF-exempt because tusd can't echo a browser cookie as a header). So
nothing in the payload is trusted: the object key must sit under the `uploads/` prefix
tusd owns, the byte count comes from the bucket rather than `Size`, the extension falls
back to the one validated at project creation, and post-finish re-runs every gate
pre-create ran — it is the call that actually spends worker and Groq budget.

Phase A note: tusd writes to the bucket itself now, so importing a finished upload is a
server-side copy rather than a file move. The threat model did not change with it.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.db import get_session
from app.models.job import Job
from app.models.project import Project
from app.models.usage import UsageEvent
from app.models.user import User
from app.projects import VIDEO_EXTS
from app.queue import enqueue_analyze
from app.quota import QuotaError, check_quota

log = logging.getLogger("clipfinder.uploads")
router = APIRouter(tags=["uploads"])

_ACK = JSONResponse({})  # 200 + empty body = "proceed"


def _reject(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"RejectUpload": True,
                         "HTTPResponse": {"StatusCode": status, "Body": msg}})


def _cookie_value(cookie_header: str, name: str) -> str | None:
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


async def _user_from_cookie(cookie_header: str, session: AsyncSession) -> User | None:
    token = _cookie_value(cookie_header, "cfa")
    if not token:
        return None
    from app.auth.backend import get_jwt_strategy
    from app.auth.manager import UserManager, get_user_db

    strategy = get_jwt_strategy()
    async for user_db in get_user_db(session):
        try:
            return await strategy.read_token(token, UserManager(user_db))
        except Exception:  # noqa: BLE001
            return None
    return None


async def _one_active_job(session: AsyncSession, user_id) -> bool:
    n = await session.scalar(
        select(func.count()).select_from(Job)
        .where(Job.user_id == user_id, Job.status.in_(("queued", "running")))
    )
    return bool(n)


def _upload_key(storage_info: dict) -> str | None:
    """The object tusd wants us to import — only if it really is one tusd wrote.

    With the S3 backend the payload carries `{"Type": "s3store", "Bucket": ..., "Key": ...}`
    instead of the filestore's `Path`. The guard is the same job as before: without it a
    caller-supplied key turns the copy below into "adopt any object in the bucket as my
    project's source, then read it at /media/<id>/source".
    """
    key = (storage_info or {}).get("Key")
    return key if storage.is_upload_key(key) else None


async def _discard(key: str) -> None:
    with contextlib.suppress(Exception):
        await run_in_threadpool(storage.delete_key, key)


async def _abandon(session: AsyncSession, project: Project, key: str, reason: str) -> JSONResponse:
    """A finished upload we can't accept. tusd ignores RejectUpload at post-finish, so drop
    the bytes and park the reason on the project where the SPA already shows errors."""
    await _discard(key)
    project.status = "failed"
    project.error = reason
    await session.commit()
    log.warning("post-finish refused for %s: %s", project.id, reason)
    return _ACK


@router.post("/api/upload/hooks")
async def tusd_hook(request: Request, session: AsyncSession = Depends(get_session)):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _ACK
    hook_type = payload.get("Type")
    event = payload.get("Event") or {}
    upload = event.get("Upload") or {}
    meta = upload.get("MetaData") or {}
    headers = (event.get("HTTPRequest") or {}).get("Header") or {}
    cookie_header = (headers.get("Cookie") or [""])[0] or request.headers.get("cookie", "")

    user = await _user_from_cookie(cookie_header, session)
    if user is None or not user.is_verified:
        return _reject(401, "Sign in to upload.")

    raw_pid = meta.get("projectId") or meta.get("projectid") or ""
    try:
        project_id = uuid.UUID(raw_pid)
    except (ValueError, TypeError):
        return _reject(400, "Missing projectId metadata.")

    project = await session.get(Project, project_id)
    if project is None or project.user_id != user.id:
        return _reject(404, "Project not found.")

    if hook_type == "pre-create":
        if project.status in ("queued", "analyzing"):
            return _reject(409, "This project is already being processed.")
        size = int(upload.get("Size") or 0)
        if await _one_active_job(session, user.id):
            return _reject(429, "You already have a job running.")
        try:
            await check_quota(session, user, "analyze")
            await check_quota(session, user, "upload", extra_bytes=size)
        except QuotaError as e:
            return _reject(429, f"You've hit your {e.limit} limit.")
        return _ACK

    if hook_type == "post-finish":
        staged = _upload_key(upload.get("Storage") or {})
        if staged is None:
            log.error("post-finish: no usable upload key for %s (storage=%r)",
                      project_id, upload.get("Storage"))
            return _ACK

        if project.status in ("queued", "analyzing"):
            # a replayed/duplicate hook — drop the bytes, but never touch the state of the
            # analysis that's already running
            log.warning("post-finish ignored for %s: already %s", project_id, project.status)
            await _discard(staged)
            return _ACK

        # `Size` is caller-supplied; the object in the bucket is the only honest number
        size = await run_in_threadpool(storage.size_of, staged)
        if not size:
            log.error("post-finish: upload object %s is missing or empty", staged)
            return _ACK

        # pre-create checked these, but that call can simply be skipped — re-check here,
        # where the worker time and the Groq budget actually get spent.
        if await _one_active_job(session, user.id):
            return await _abandon(session, project, staged, "You already have a job running.")
        try:
            await check_quota(session, user, "analyze")
            await check_quota(session, user, "upload", extra_bytes=size)
        except QuotaError as e:
            return await _abandon(session, project, staged, f"You've hit your {e.limit} limit.")

        # an unvetted extension would name the object; fall back to the one create_project
        # already validated against VIDEO_EXTS
        ext = Path(meta.get("filename") or "").suffix.lower()
        if ext not in VIDEO_EXTS:
            ext = project.source_ext if project.source_ext in VIDEO_EXTS else ".mp4"
        # server-side copy, then drop the upload object — no bytes through this process
        await run_in_threadpool(storage.copy, staged, storage.source_key(user.id, project.id, ext))
        await _discard(staged)

        project.source_ext = ext
        project.source_bytes = size
        project.source_filename = (meta.get("filename") or project.source_filename)[:255]
        project.status = "queued"
        project.error = None
        job = Job(user_id=user.id, project_id=project.id, kind="analyze", status="queued",
                  payload={"taste": project.taste} if project.taste else None)
        session.add(job)
        session.add(UsageEvent(user_id=user.id, project_id=project.id,
                               kind="upload_bytes", quantity=size))
        await session.commit()
        job.arq_job_id = await enqueue_analyze(job.id)
        await session.commit()
        log.info("resumable upload finished for %s (%d bytes) → analyze %s", project_id, size, job.id)
        return _ACK

    return _ACK
