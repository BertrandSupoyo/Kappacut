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
from app.quota import minutes_cap, monthly_minutes, project_count, storage_bytes, storage_cap_bytes
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
            "created_at": u.created_at.isoformat(),
            "projects": await project_count(session, u.id),
            "minutes_used": await monthly_minutes(session, u.id),
            "minutes_cap": minutes_cap(u),
            "storage_mb": round(await storage_bytes(session, u.id) / 1024**2, 1),
            "storage_cap_mb": round(storage_cap_bytes(u) / 1024**2),
        })
    return {"users": out}


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


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    j = await session.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "no such job")
    if j.arq_job_id:
        await abort_job(j.arq_job_id)
    if j.status in ("queued", "running"):
        j.status = "error"
        j.error = "cancelled by admin"
        await session.commit()
    return {"ok": True, "status": j.status}


@router.get("/queue")
async def queue_stats(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(
        select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)
    )).all()
    return {"jobs": [{"kind": k, "status": s, "count": c} for k, s, c in rows]}
