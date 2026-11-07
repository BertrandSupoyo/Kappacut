"""Superuser-only endpoints behind the Admin dashboard: its overview, users and their limits,
jobs, invites, copyright takedowns."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import BigInteger, case, cast, delete, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.admin_stats import build_overview, job_json, like_pattern
from app.auth import current_superuser
from app.config import settings
from app.db import get_session
from app.email import send_invite_email, send_reinstated_notice, send_suspension_notice, send_takedown_notice
from app.models.job import Job, RenderOutput
from app.models.launch import Invite, Takedown
from app.models.project import Project
from app.models.sfx import SfxAsset
from app.models.usage import UsageEvent
from app.models.user import User
from app.quota import _month_start, is_unlimited, minutes_cap, storage_cap_bytes

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_superuser)])


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)) -> dict:
    return await build_overview(session)


USER_FILTERS = ("all", "unverified", "suspended", "admins", "removals", "near_limit")
USER_SORTS = ("newest", "active", "minutes", "storage", "removals")


@router.get("/users")
async def list_users(
    q: str = "",
    show: str = "all",
    sort: str = "newest",
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """One query however many users there are; it was three per user, which a few thousand accounts
    would have turned into a slow page. The numbers have to match app.quota's storage_bytes and
    monthly_minutes, which are what limits are enforced on."""
    if show not in USER_FILTERS or sort not in USER_SORTS:
        raise HTTPException(400, "unknown filter or sort")
    limit, offset = max(1, min(limit, 200)), max(0, offset)

    projects = (select(Project.user_id.label("uid"), func.count().label("n"),
                       func.coalesce(func.sum(Project.source_bytes), 0).label("bytes"),
                       func.max(Project.last_opened_at).label("opened"))
                .group_by(Project.user_id).subquery())
    rendered = (select(Project.user_id.label("uid"), func.sum(RenderOutput.bytes).label("bytes"))
                .join(RenderOutput, RenderOutput.project_id == Project.id)
                .group_by(Project.user_id).subquery())
    sounds = select(SfxAsset.user_id.label("uid"), func.sum(SfxAsset.bytes).label("bytes")).group_by(SfxAsset.user_id).subquery()
    usage = (select(UsageEvent.user_id.label("uid"), func.sum(UsageEvent.quantity).label("secs"))
             .where(UsageEvent.kind == "transcribe_seconds", UsageEvent.created_at >= _month_start())
             .group_by(UsageEvent.user_id).subquery())
    removals = select(Takedown.user_id.label("uid"), func.count().label("n")).group_by(Takedown.user_id).subquery()
    jobs = select(Job.user_id.label("uid"), func.max(Job.created_at).label("last")).group_by(Job.user_id).subquery()

    project_count = func.coalesce(projects.c.n, 0)
    stored = (func.coalesce(projects.c.bytes, 0) + func.coalesce(rendered.c.bytes, 0)
              + func.coalesce(sounds.c.bytes, 0))
    seconds = func.coalesce(usage.c.secs, 0)
    taken_down = func.coalesce(removals.c.n, 0)
    last_active = func.greatest(projects.c.opened, jobs.c.last)        # GREATEST ignores NULLs

    conditions = []
    if q.strip():
        conditions.append(User.email.ilike(like_pattern(q), escape="\\"))
    if show == "unverified":
        conditions.append(User.is_verified.is_(False))
    elif show == "suspended":
        conditions.append(User.is_active.is_(False))
    elif show == "admins":
        conditions.append(User.is_superuser.is_(True))
    elif show == "removals":
        conditions.append(taken_down > 0)
    elif show == "near_limit":                      # 80% of either cap; admins have none
        minutes_limit = func.coalesce(User.quota_minutes_override, settings.quota_minutes_per_month)
        # bigint: a 32-bit column times 1024^2 overflows at an override of 2 GB
        storage_limit = case((User.quota_storage_mb_override.isnot(None),
                              cast(User.quota_storage_mb_override, BigInteger) * 1024**2),
                             else_=literal(settings.quota_storage_bytes, BigInteger))
        conditions.append(User.is_superuser.is_(False))
        conditions.append(or_(seconds >= minutes_limit * 48, stored * 5 >= storage_limit * 4))

    order = {"newest": (), "active": (last_active.desc().nulls_last(),), "minutes": (seconds.desc(),),
             "storage": (stored.desc(),), "removals": (taken_down.desc(),)}[sort]
    base = (select(User, project_count.label("projects"), stored.label("stored"), seconds.label("seconds"),
                   taken_down.label("removals"), last_active.label("last_active"))
            .outerjoin(projects, projects.c.uid == User.id)
            .outerjoin(rendered, rendered.c.uid == User.id)
            .outerjoin(sounds, sounds.c.uid == User.id)
            .outerjoin(usage, usage.c.uid == User.id)
            .outerjoin(removals, removals.c.uid == User.id)
            .outerjoin(jobs, jobs.c.uid == User.id)
            .where(*conditions))
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (await session.execute(
        base.order_by(*order, User.created_at.desc(), User.id).limit(limit).offset(offset)
    )).all()
    return {
        "total": total, "limit": limit, "offset": offset,
        "users": [{
            "id": str(u.id),
            "email": u.email,
            "is_verified": u.is_verified,
            "is_superuser": u.is_superuser,
            "suspended": not u.is_active,
            "suspended_at": u.suspended_at.isoformat() if u.suspended_at else None,
            "suspended_reason": u.suspended_reason,
            "unlimited": is_unlimited(u),
            "created_at": u.created_at.isoformat(),
            "last_active": active.isoformat() if active else None,
            "terms_version": u.terms_version,
            "terms_accepted_at": u.terms_accepted_at.isoformat() if u.terms_accepted_at else None,
            "projects": int(n),
            "minutes_used": round(float(secs) / 60, 2),
            "minutes_cap": minutes_cap(u),
            "storage_mb": round(int(used) / 1024**2, 1),
            "storage_cap_mb": round(storage_cap_bytes(u) / 1024**2),
            "minutes_override": u.quota_minutes_override,
            "storage_mb_override": u.quota_storage_mb_override,
            "takedowns": int(taken),
        } for u, n, used, secs, taken, active in rows],
    }


@router.get("/users/{user_id}/projects")
async def list_user_projects(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    """Names only, enough to find what a copyright notice is about."""
    rows = (await session.execute(
        select(Project).where(Project.user_id == user_id).order_by(Project.created_at.desc())
    )).scalars().all()
    return {"projects": [{"id": str(p.id), "name": p.name, "source_filename": p.source_filename,
                          "status": p.status, "created_at": p.created_at.isoformat()} for p in rows]}


JOB_FILTERS = {
    "active": Job.status.in_(("queued", "running")),
    "failed": (Job.status == "error") & Job.error_detail.isnot(None),
    "stopped": (Job.status == "error") & Job.error_detail.is_(None),
    "done": Job.status == "done",
}


@router.get("/jobs")
async def list_jobs(
    status: str = "all",
    kind: str = "all",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Active jobs first, then the most recent. `counts` ignores the status filter, so the filter
    buttons can always say how many each would show."""
    if status != "all" and status not in JOB_FILTERS:
        raise HTTPException(400, "unknown status")
    if kind not in ("all", "analyze", "render"):
        raise HTTPException(400, "unknown kind")
    limit, offset = max(1, min(limit, 200)), max(0, offset)

    scope = []
    if kind != "all":
        scope.append(Job.kind == kind)
    if q.strip():
        pattern = like_pattern(q)
        scope.append(or_(User.email.ilike(pattern, escape="\\"), Project.name.ilike(pattern, escape="\\")))

    def joined(query):
        return (query.join(User, User.id == Job.user_id)
                .outerjoin(Project, Project.id == Job.project_id).where(*scope))

    counts = dict((await session.execute(joined(select(
        func.count().label("all"), *[func.count().filter(cond).label(name) for name, cond in JOB_FILTERS.items()]
    ).select_from(Job)))).one()._mapping)

    active = Job.status.in_(("queued", "running"))
    rows = (await session.execute(
        joined(select(Job, User.email, Project.name))
        .where(*([JOB_FILTERS[status]] if status != "all" else []))
        .order_by(active.desc(), Job.created_at.desc()).limit(limit).offset(offset)
    )).all()
    return {"jobs": [job_json(*row) for row in rows], "counts": counts, "limit": limit, "offset": offset,
            "total": counts[status]}


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


class SuspendIn(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/users/{user_id}/suspend")
async def suspend_user(user_id: uuid.UUID, body: SuspendIn, session: AsyncSession = Depends(get_session)) -> dict:
    """Blocks sign-in and every signed-in route at once (they all require an active user) and keeps
    the account's data. The Terms promise to say why unless the law or safety stops us, so a reason
    is emailed, and leaving it blank sends nothing."""
    from app.jobs import stop_jobs      # local import: jobs pulls in the whole pipeline

    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(404, "no such user")
    if u.is_superuser:
        raise HTTPException(400, "Admin accounts can't be suspended here.")
    if not u.is_active:
        return {"ok": True}
    email, reason = u.email, body.reason.strip()
    u.is_active, u.suspended_at, u.suspended_reason = False, dt.datetime.now(dt.UTC), reason or None
    await session.commit()          # first, so nothing new can start while the old jobs stop
    await stop_jobs("This account was suspended.", Job.user_id == user_id)
    if reason:
        await send_suspension_notice(email, reason)
    return {"ok": True}


@router.post("/users/{user_id}/reinstate")
async def reinstate_user(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(404, "no such user")
    if u.is_active:
        return {"ok": True}
    told = u.suspended_reason is not None
    email = u.email
    u.is_active, u.suspended_at, u.suspended_reason = True, None, None
    await session.commit()
    if told:
        await send_reinstated_notice(email)
    return {"ok": True}


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


@router.get("/projects")
async def find_projects(q: str = "", session: AsyncSession = Depends(get_session)) -> dict:
    """A copyright notice names a video, not an account, so this searches names and file names
    across everyone, as well as owners. Names only: the video itself stays with its owner."""
    if len(q.strip()) < 2:
        return {"projects": []}
    pattern = like_pattern(q)
    rows = (await session.execute(
        select(Project, User.email).join(User, User.id == Project.user_id)
        .where(or_(Project.name.ilike(pattern, escape="\\"), Project.source_filename.ilike(pattern, escape="\\"),
                   User.email.ilike(pattern, escape="\\")))
        .order_by(Project.created_at.desc()).limit(25)
    )).all()
    return {"projects": [{"id": str(p.id), "name": p.name, "source_filename": p.source_filename, "status": p.status,
                          "owner": email, "created_at": p.created_at.isoformat()} for p, email in rows]}


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
