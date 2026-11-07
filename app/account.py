"""A user's own account: usage against their caps, a copy of their data, and deleting it all."""
from __future__ import annotations

import datetime as dt
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.auth import current_verified_user
from app.auth.backend import cookie_transport
from app.auth.manager import get_user_manager
from app.config import settings
from app.db import get_session
from app.models.job import Job, RenderOutput
from app.models.launch import Invite, Takedown
from app.models.project import Analysis, Project, ProjectEdit
from app.models.sfx import SfxAsset
from app.models.training import VideoLabel
from app.models.usage import UsageEvent
from app.models.user import User
from app.quota import (
    is_unlimited,
    minutes_cap,
    monthly_minutes,
    project_count,
    renders_today,
    storage_bytes,
    storage_cap_bytes,
)
from app.ratelimit import rate_limit

log = logging.getLogger("kappacut.account")
router = APIRouter(prefix="/api/users/me", tags=["account"])

EXPORT_LINK_HOURS = 24


@router.get("/usage")
async def my_usage(
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {
        "plan": "admin" if is_unlimited(user) else "free",
        "unlimited": is_unlimited(user),         # caps below are not enforced for this account
        "minutes": {"used": await monthly_minutes(session, user.id), "cap": minutes_cap(user)},
        "storage_mb": {
            "used": round(await storage_bytes(session, user.id) / 1024**2, 1),
            "cap": round(storage_cap_bytes(user) / 1024**2),
        },
        "renders_today": {"used": await renders_today(session, user.id),
                          "cap": settings.quota_renders_per_day},
        "projects": {"used": await project_count(session, user.id),
                     "cap": settings.quota_projects},
        "max_upload_mb": round(settings.quota_max_upload_bytes / 1024**2),
    }


def _when(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def _by_project(rows: list) -> dict:
    grouped: dict = {}
    for row in rows:
        grouped.setdefault(row.project_id, []).append(row)
    return grouped


async def build_export(session: AsyncSession, user: User) -> dict:
    """Everything stored about this account, in one document. Written as a list of what exists
    rather than a dump of the tables, so a new column doesn't reach users without a decision."""
    async def rows(query) -> list:
        return list((await session.execute(query)).scalars().all())

    projects = await rows(select(Project).where(Project.user_id == user.id).order_by(Project.created_at))
    ids = [p.id for p in projects]
    analyses = {a.project_id: a for a in await rows(select(Analysis).where(Analysis.project_id.in_(ids)))}
    edits = {e.project_id: e for e in await rows(select(ProjectEdit).where(ProjectEdit.project_id.in_(ids)))}
    labels = {v.project_id: v for v in await rows(select(VideoLabel).where(VideoLabel.project_id.in_(ids)))}
    jobs = _by_project(await rows(select(Job).where(Job.user_id == user.id).order_by(Job.created_at)))
    renders = _by_project(await rows(select(RenderOutput).where(RenderOutput.project_id.in_(ids))
                                     .order_by(RenderOutput.created_at)))

    def project(p: Project) -> dict:
        a, e, v = analyses.get(p.id), edits.get(p.id), labels.get(p.id)
        return {
            "name": p.name, "source_filename": p.source_filename, "source_bytes": p.source_bytes,
            "duration_seconds": p.duration_seconds, "status": p.status, "taste": p.taste,
            "clip_count": p.clip_count, "content_type": p.content_type,
            "created_at": _when(p.created_at), "last_opened_at": _when(p.last_opened_at),
            "analysis": a and {"transcript": a.segments, "clips": a.clips, "category": a.category,
                               "created_at": _when(a.created_at)},
            "editor_state": e and e.state,
            "training_label": v and {"moments": v.moments, "finished": v.done},
            "jobs": [{"kind": j.kind, "status": j.status, "error": j.error, "created_at": _when(j.created_at),
                      "finished_at": _when(j.finished_at)} for j in jobs.get(p.id, [])],
            "rendered_clips": [{"file": r.filename, "seconds": r.duration, "vertical": r.vertical,
                                "captions": r.captions, "bytes": r.bytes, "created_at": _when(r.created_at)}
                               for r in renders.get(p.id, [])],
        }

    usage = await rows(select(UsageEvent).where(UsageEvent.user_id == user.id).order_by(UsageEvent.created_at))
    sounds = await rows(select(SfxAsset).where(SfxAsset.user_id == user.id))
    notices = await rows(select(Takedown).where(Takedown.user_id == user.id))
    return {
        "exported_at": dt.datetime.now(dt.UTC).isoformat(),
        "account": {"email": user.email, "display_name": user.display_name, "created_at": _when(user.created_at),
                    "email_verified": user.is_verified, "terms_version": user.terms_version,
                    "terms_accepted_at": _when(user.terms_accepted_at)},
        "projects": [project(p) for p in projects],
        "sound_effects": [{"name": s.name, "file": s.filename, "bytes": s.bytes} for s in sounds],
        "usage": [{"kind": u.kind, "quantity": u.quantity, "at": _when(u.created_at)} for u in usage],
        "copyright_removals": [{"project": t.project_name, "reference": t.reference, "at": _when(t.created_at)}
                               for t in notices],
        "files_note": f"These links stop working {EXPORT_LINK_HOURS} hours after the export, and anyone "
                      "who has one can download that file, so keep this document private.",
        "files": await run_in_threadpool(storage.export_links, user.id, EXPORT_LINK_HOURS * 3600),
    }


@router.get("/export", dependencies=[Depends(rate_limit("export", 5, 60 * 60))])
async def export_my_data(
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """A download, so the browser can save it from a plain link. Videos go by signed link, not
    in the file: a 2 GB source has no business passing through this process."""
    name = f"kappacut-export-{dt.datetime.now(dt.UTC):%Y-%m-%d}.json"
    return Response(json.dumps(await build_export(session, user), ensure_ascii=False, indent=1),
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


class DeleteAccountIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@router.post("/delete", dependencies=[Depends(rate_limit("delete-account", 5, 60 * 60))])
async def delete_my_account(
    body: DeleteAccountIn,
    response: Response,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
    manager=Depends(get_user_manager),
) -> dict:
    """POST, not DELETE /api/users/me: fastapi-users' superuser DELETE /api/users/{id} route
    would catch that path. The password is asked for because this can't be undone, and a
    session left open on a shared computer shouldn't be enough."""
    from app.jobs import stop_jobs      # local import: jobs pulls in the whole pipeline

    if user.is_superuser:
        raise HTTPException(400, "Admin accounts can't be deleted here. Remove the admin role first.")
    if not manager.password_helper.verify_and_update(body.password, user.hashed_password)[0]:
        raise HTTPException(400, "That password isn't right.")

    # Media before rows, as in Admin > Delete: a failure leaves the account intact to retry,
    # rather than files nobody can find any more. A clip being cut right now can't write back
    # after the sweep: run_render_clip checks its job first, and removes what it wrote if the
    # job ended during the upload.
    user_id = user.id
    await stop_jobs("This account was deleted.", Job.user_id == user_id)
    objects = await run_in_threadpool(storage.delete_user_media, user_id)
    await session.execute(delete(Invite).where(Invite.email == user.email))
    await session.delete(user)          # the database cascades to everything else
    await session.commit()
    response.delete_cookie(cookie_transport.cookie_name, path="/", secure=settings.cookie_secure,
                           httponly=True, samesite="lax")
    log.info("account %s deleted itself (%d objects)", user_id, objects)
    return {"ok": True, "objects_deleted": objects}
