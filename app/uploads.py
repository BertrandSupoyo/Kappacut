"""tusd resumable-upload hooks.

tusd (the `/files/*` service) POSTs here before creating an upload (`pre-create` — we
authorise + quota-check) and after it finishes (`post-finish` — we move the file into the
project dir and enqueue analysis). The client's cookie is forwarded via
`-hooks-http-forward-headers Cookie`.

Flow:  SPA `POST /api/projects` → gets an id → Uppy/tus upload to `/files/` with metadata
`{projectId, filename}` → tusd calls these hooks.
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.db import get_session
from app.models.job import Job
from app.models.project import Project
from app.models.usage import UsageEvent
from app.models.user import User
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
        size = int(upload.get("Size") or upload.get("Offset") or 0)
        staged = (upload.get("Storage") or {}).get("Path")
        if not staged or not Path(staged).is_file():
            log.error("post-finish: staged file missing for %s", project_id)
            return _ACK
        ext = Path(meta.get("filename") or "video.mp4").suffix.lower() or ".mp4"
        dest = storage.source_path(user.id, project.id, ext)
        os.replace(staged, dest)                       # same volume → atomic
        Path(staged + ".info").unlink(missing_ok=True)

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
