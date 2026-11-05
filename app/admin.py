"""Superuser-only endpoints — user list + usage, quota overrides, force delete, job cancel,
invites, copyright takedowns."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.auth import current_superuser
from app.config import settings
from app.db import get_session
from app.email import send_invite_email, send_takedown_notice
from app.models.job import Job
from app.models.launch import Invite, Takedown
from app.models.project import Project
from app.models.user import User
from app.quota import is_unlimited, minutes_cap, monthly_minutes, project_count, storage_bytes, storage_cap_bytes

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_superuser)])


@router.get("/users")
async def list_users(session: AsyncSession = Depends(get_session)) -> dict:
    users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    removals = dict((await session.execute(
        select(Takedown.user_id, func.count()).group_by(Takedown.user_id))).all())
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
            "takedowns": removals.get(u.id, 0),
        })
    return {"users": out}


@router.get("/users/{user_id}/projects")
async def list_user_projects(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    """Names only, enough to find what a copyright notice is about."""
    rows = (await session.execute(
        select(Project).where(Project.user_id == user_id).order_by(Project.created_at.desc())
    )).scalars().all()
    return {"projects": [{"id": str(p.id), "name": p.name, "source_filename": p.source_filename,
                          "status": p.status, "created_at": p.created_at.isoformat()} for p in rows]}


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
        "msg": j.progress_msg or j.stage, "error": j.error, "detail": j.error_detail,
        "user": email, "project": name, "project_id": str(j.project_id) if j.project_id else None,
        "created_at": j.created_at.isoformat(),
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    } for j, email, name in rows]}


def _override(value: object, field: str) -> int | None:
    """A quota override from the admin form: empty / 0 = back to the plan default."""
    if value in (None, "", 0, "0"):
        return None
    try:
        n = int(str(value).strip())
    except ValueError:
        raise HTTPException(400, f"{field} must be a whole number")
    if not 0 < n <= 2**31 - 1:                  # the column is a 32-bit integer
        raise HTTPException(400, f"{field} must be a positive number")
    return n


@router.post("/users/{user_id}/quota")
async def set_quota(
    user_id: uuid.UUID, body: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(404, "no such user")
    if "minutes" in body:
        u.quota_minutes_override = _override(body["minutes"], "minutes")
    if "storage_mb" in body:
        u.quota_storage_mb_override = _override(body["storage_mb"], "storage_mb")
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


class TakedownIn(BaseModel):
    reference: str = Field(min_length=1, max_length=500)


@router.post("/projects/{project_id}/takedown")
async def take_down_project(
    project_id: uuid.UUID,
    body: TakedownIn,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove a project after a copyright notice. Unlike a plain delete this keeps a record
    (a repeat-infringer policy needs the count, and it has to survive the account) and tells
    the owner, who is entitled to dispute it."""
    from app.jobs import stop_jobs      # local import: jobs pulls in the whole pipeline

    reference = body.reference.strip()
    if not reference:
        raise HTTPException(400, "Say which notice this is for.")
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "no such project")
    owner = await session.get(User, p.user_id)
    name, email, owner_id = p.name, owner.email, owner.id

    await stop_jobs("This project was removed after a copyright notice.", Job.project_id == p.id)
    await run_in_threadpool(storage.delete_project_media, p.user_id, p.id)
    session.add(Takedown(user_id=owner_id, user_email=email, project_name=name,
                         reference=reference, created_by=admin.id))
    await session.delete(p)
    await session.commit()
    await send_takedown_notice(email, name, reference)
    total = await session.scalar(select(func.count()).select_from(Takedown).where(Takedown.user_id == owner_id))
    return {"ok": True, "takedowns": total}


@router.get("/takedowns")
async def list_takedowns(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(
        select(Takedown).order_by(Takedown.created_at.desc()).limit(200)
    )).scalars().all()
    return {"takedowns": [{"id": str(t.id), "user_email": t.user_email, "project": t.project_name,
                           "reference": t.reference, "created_at": t.created_at.isoformat()} for t in rows]}


@router.get("/invites")
async def list_invites(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(
        select(Invite, User.id).outerjoin(User, User.email == Invite.email).order_by(Invite.created_at.desc())
    )).all()
    return {"invite_only": settings.invite_only,
            "invites": [{"id": str(i.id), "email": i.email, "registered": uid is not None,
                         "created_at": i.created_at.isoformat()} for i, uid in rows]}


class InviteIn(BaseModel):
    email: EmailStr


@router.post("/invites")
async def create_invite(
    body: InviteIn,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Inviting someone twice is harmless and sends no second email."""
    created = await session.scalar(
        insert(Invite).values(id=uuid.uuid4(), email=body.email, created_by=admin.id)
        .on_conflict_do_nothing(index_elements=[Invite.email]).returning(Invite.id))
    await session.commit()
    if created:
        await send_invite_email(body.email)
    return {"ok": True, "created": created is not None}


@router.delete("/invites/{invite_id}")
async def delete_invite(invite_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    """Stops this email signing up. An account it already made keeps working."""
    invite = await session.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(404, "no such invite")
    await session.delete(invite)
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
    await session.execute(delete(Invite).where(Invite.email == u.email))
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
    kind, project_id, live = j.kind, j.project_id, j.status in ("queued", "running")
    if live:
        # the same exit as a worker failure: job -> error, and an analyze job's project goes
        # back to its previous clips (or "failed" if it had none). Setting only the job row,
        # as this did before, left the project "analyzing" forever. The task running it sees
        # the row change within seconds and kills its ffmpeg (app.jobs._watch_job).
        await _fail(job_id, "An administrator stopped this job.", project_id if kind == "analyze" else None)
    await session.refresh(j)
    return {"ok": True, "status": j.status}


@router.get("/queue")
async def queue_stats(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(
        select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)
    )).all()
    return {"jobs": [{"kind": k, "status": s, "count": c} for k, s, c in rows]}
