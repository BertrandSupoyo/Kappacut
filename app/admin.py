"""Superuser-only endpoints — user list + usage, quota overrides, force delete, job cancel."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.auth import current_superuser
from app.db import get_session
from app.models.job import Job
from app.models.project import Project
from app.models.user import User
from app.quota import is_unlimited, minutes_cap, monthly_minutes, project_count, storage_bytes, storage_cap_bytes
from app.queue import abort_job

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_superuser)])


@router.get("/users")
async def list_users(session: AsyncSession = Depends(get_session)) -> dict:
    users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    out = []
    for u in users:
        out.append({
            "id": str(u.id),
            "email": u.email,
            "is_verified": u.is_verified,
            "is_superuser": u.is_superuser,
            "unlimited": is_unlimited(u),
            "created_at": u.created_at.isoformat(),
            "projects": await project_count(session, u.id),
            "minutes_used": await monthly_minutes(session, u.id),
            "minutes_cap": minutes_cap(u),
            "storage_mb": round(await storage_bytes(session, u.id) / 1024**2, 1),
            "storage_cap_mb": round(storage_cap_bytes(u) / 1024**2),
            "minutes_override": u.quota_minutes_override,
            "storage_mb_override": u.quota_storage_mb_override,
        })
    return {"users": out}


@router.get("/jobs")
async def list_jobs(limit: int = 50, session: AsyncSession = Depends(get_session)) -> dict:
    """Active jobs first, then the most recent finished ones - what the Admin page's queue
    table shows, with a Cancel button on anything queued or running."""
    limit = max(1, min(int(limit), 200))
    active = (Job.status.in_(("queued", "running")))
    rows = (await session.execute(
        select(Job, User.email, Project.name)
        .join(User, User.id == Job.user_id)
        .outerjoin(Project, Project.id == Job.project_id)
        .order_by(active.desc(), Job.created_at.desc())
        .limit(limit)
    )).all()
    return {"jobs": [{
        "id": str(j.id), "kind": j.kind, "status": j.status, "pct": j.progress_pct,
        "msg": j.progress_msg or j.stage, "error": j.error,
        "user": email, "project": name, "project_id": str(j.project_id) if j.project_id else None,
        "created_at": j.created_at.isoformat(),
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    } for j, email, name in rows]}


@router.post("/users/{user_id}/quota")
async def set_quota(
    user_id: uuid.UUID, body: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(404, "no such user")
    if "minutes" in body:
        u.quota_minutes_override = int(body["minutes"]) if body["minutes"] else None
    if "storage_mb" in body:
        u.quota_storage_mb_override = int(body["storage_mb"]) if body["storage_mb"] else None
    await session.commit()
    return {"ok": True, "minutes_override": u.quota_minutes_override,
            "storage_mb_override": u.quota_storage_mb_override}


@router.delete("/projects/{project_id}")
async def force_delete_project(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "no such project")
    await run_in_threadpool(storage.delete_project_media, p.user_id, p.id)
    await session.delete(p)
    await session.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove an account and everything it owns. The DB rows cascade from `users`; the
    bucket doesn't, so the media goes first — a failure there leaves the account intact to
    retry, rather than orphaning objects nobody can find any more."""
    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(404, "no such user")
    if u.id == admin.id or u.is_superuser:
        raise HTTPException(400, "superusers can't be deleted here — demote them first")
    live = await session.scalar(
        select(func.count()).select_from(Job)
        .where(Job.user_id == u.id, Job.status.in_(("queued", "running")))
    )
    if live:
        # a running job would write clips back under the prefix after it was swept
        raise HTTPException(409, "this user has a job queued or running — cancel it first")
    objects = await run_in_threadpool(storage.delete_user_media, u.id)
    await session.delete(u)
    await session.commit()
    return {"ok": True, "objects_deleted": objects}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    from app.jobs import _fail

    j = await session.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "no such job")
    kind, project_id, arq_id, live = j.kind, j.project_id, j.arq_job_id, j.status in ("queued", "running")
    if arq_id:
        await abort_job(arq_id)
    if live:
        # the same exit as a worker failure: job -> error, and an analyze job's project goes
        # back to its previous clips (or "failed" if it had none). Setting only the job row,
        # as this did before, left the project "analyzing" forever.
        await _fail(job_id, "cancelled by admin", project_id if kind == "analyze" else None)
    await session.refresh(j)
    return {"ok": True, "status": j.status}


@router.get("/queue")
async def queue_stats(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(
        select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)
    )).all()
    return {"jobs": [{"kind": k, "status": s, "count": c} for k, s, c in rows]}
