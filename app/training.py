"""Admin > Training: mark the good moments in a video, score the AI against them, train the weights.

An admin labels only their own projects: the video route serves a project's owner and nobody else,
and this page isn't a reason to change that. Scores and training use every finished label, and
Keep/Skip choices from all users (numbers only, no video).

The AI's picks stay hidden until a label is finished, here on the server rather than only in the
page: marks made after seeing the picks measure agreement with the AI, not what it missed.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import kappacut as cf
from app.auth import current_superuser
from app.db import get_session
from app.evaluate import (
    _score,
    build_moment_report,
    build_report,
    evaluate_moments,
    label_is_current,
    label_spans,
    load_labels,
    matches_moment,
)
from app.models.project import Analysis, Project
from app.models.training import TrainingRun, VideoLabel
from app.models.user import User
from app.projects import _owned
from app.train_weights import MIN_VIDEOS, load_videos, result_json, train

router = APIRouter(prefix="/api/admin/training", tags=["admin"], dependencies=[Depends(current_superuser)])

MAX_MOMENTS = 200
MIN_MOMENT_SECONDS = 1.0
END_SLACK = 1.0            # a player's currentTime can pass the probed duration by a frame or two


class MomentIn(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    note: str = Field(default="", max_length=300)


class LabelIn(BaseModel):
    moments: list[MomentIn] = Field(max_length=MAX_MOMENTS)
    done: bool = False
    blind: bool = False


def clean_moments(moments: list[MomentIn], duration: float | None) -> list[dict]:
    """Sorted and checked. Overlaps are refused: one stretch marked twice would count as two
    moments, and the AI could only ever "find" both by finding the same thing."""
    out = sorted(({"start": round(m.start, 2), "end": round(m.end, 2), "note": m.note.strip()} for m in moments),
                 key=lambda m: m["start"])
    for m in out:
        if m["end"] - m["start"] < MIN_MOMENT_SECONDS:
            raise HTTPException(400, f"a moment must be at least {MIN_MOMENT_SECONDS:g} second long")
        if duration and m["end"] > duration + END_SLACK:
            raise HTTPException(400, "a moment ends after the video does")
    for a, b in zip(out, out[1:]):
        if b["start"] < a["end"]:
            raise HTTPException(400, "two moments overlap: merge them or move one")
    return out


def apply_label(row, moments: list[dict], *, done: bool, blind: bool, source_bytes: int | None) -> None:
    """Finishing shows the AI's picks, and that can't be unseen: once revealed, a video never
    counts as blind again, however the label is edited afterwards."""
    row.moments = moments
    row.source_bytes = source_bytes
    row.blind = bool(done and blind and not row.revealed)
    if done:
        row.revealed = True
    row.done = done


def label_status(label, source_bytes: int | None) -> str:
    if label is None:
        return "none"
    if not label_is_current(label.source_bytes, source_bytes):
        return "stale"
    return "done" if label.done else "draft"


def comparison(clips: list[dict], moments: list[dict]) -> dict:
    spans = [(float(c["start_seconds"]), float(c["end_seconds"])) for c in clips]
    marked = label_spans(moments)
    return {
        "stats": evaluate_moments(clips, marked),
        "picks": [{
            "start": a, "end": b, "title": c.get("title") or "", "score": _score(c),
            "good": any(matches_moment(a, b, *m) for m in marked),
        } for c, (a, b) in zip(clips, spans)],
        "moments": [{**m, "found": any(matches_moment(a, b, *span) for a, b in spans)}
                    for m, span in zip(moments, marked)],
    }


def video_row(project: Project, clips: list[dict] | None, category: str | None, label) -> dict:
    """One line of the list: no pick count or times until the label is finished."""
    status = label_status(label, project.source_bytes)
    return {
        "id": str(project.id), "name": project.name, "seconds": project.duration_seconds,
        "category": category, "status": status,
        "moments": len(label.moments or []) if label else 0,
        "blind": bool(label and label.blind),
        "updated_at": label.updated_at.isoformat() if label and label.updated_at else None,
        "score": evaluate_moments(clips or [], label_spans(label.moments)) if status == "done" else None,
    }


def video_detail(project: Project, clips: list[dict] | None, category: str | None, label) -> dict:
    status = label_status(label, project.source_bytes)
    return {
        "id": str(project.id), "name": project.name, "seconds": project.duration_seconds,
        "category": category, "status": status,
        "moments": (label.moments or []) if label else [],
        "blind": bool(label and label.blind),
        "revealed": bool(label and label.revealed),
        "comparison": comparison(clips or [], label.moments or []) if status == "done" else None,
    }


async def _video(project_id: uuid.UUID, user: User, session: AsyncSession):
    project = await _owned(project_id, user, session)
    row = (await session.execute(
        select(Analysis.clips, Analysis.category).where(Analysis.project_id == project.id)
    )).first()
    if row is None:
        raise HTTPException(409, "this video hasn't been analysed yet")
    return project, row.clips, row.category, await session.get(VideoLabel, project.id)


@router.get("/videos")
async def list_videos(
    user: User = Depends(current_superuser), session: AsyncSession = Depends(get_session)
) -> dict:
    rows = (await session.execute(
        select(Project, Analysis.clips, Analysis.category, VideoLabel)
        .join(Analysis, Analysis.project_id == Project.id)
        .outerjoin(VideoLabel, VideoLabel.project_id == Project.id)
        .where(Project.user_id == user.id)
        .order_by(Project.created_at.desc())
    )).all()
    return {"videos": [video_row(*r) for r in rows]}


@router.get("/videos/{project_id}")
async def get_video(
    project_id: uuid.UUID,
    user: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return video_detail(*await _video(project_id, user, session))


@router.put("/videos/{project_id}/label")
async def save_label(
    project_id: uuid.UUID,
    body: LabelIn,
    user: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_session),
) -> dict:
    project, clips, category, label = await _video(project_id, user, session)
    moments = clean_moments(body.moments, project.duration_seconds)
    if label is None:
        label = VideoLabel(project_id=project.id, revealed=False)
        session.add(label)
    apply_label(label, moments, done=body.done, blind=body.blind, source_bytes=project.source_bytes)
    await session.commit()
    await session.refresh(label)
    return video_detail(project, clips, category, label)


def _usage(report) -> dict:
    t = report.total()
    return {"analysed": report.analysed, "reviewed": report.reviewed,
            **{k: t[k] for k in ("picks", "kept", "rendered", "trimmed", "reframed", "pairs", "pairs_right")},
            "mean_abs_shift": t["mean_abs_shift"]}


@router.get("/summary")
async def summary(session: AsyncSession = Depends(get_session)) -> dict:
    moments = await build_moment_report(session)
    videos = await load_videos(session, await load_labels(session))
    return {
        "score": moments.total(),
        "blind_score": moments.total(blind_only=True),
        "stale": moments.stale,
        "training": {"videos": len(videos), "hand_labelled": sum(v.hand_labelled for v in videos),
                     "pairs": sum(len(v.pairs) for v in videos), "needed": MIN_VIDEOS},
        "usage": _usage(await build_report(session)),
        "weights": cf.SCORE_WEIGHTS,
    }


def _run_json(run: TrainingRun) -> dict:
    return {"id": str(run.id), "created_at": run.created_at.isoformat(), "score": run.score, "result": run.result}


@router.post("/runs")
async def create_run(
    user: User = Depends(current_superuser), session: AsyncSession = Depends(get_session)
) -> dict:
    """Records a result and adopts nothing. Takes seconds of CPU with many videos, so it runs
    off the event loop."""
    moments = await build_moment_report(session)
    videos = await load_videos(session, await load_labels(session))
    result = await run_in_threadpool(train, videos)
    run = TrainingRun(created_by=user.id, score=moments.total(), result=result_json(result))
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return _run_json(run)


@router.get("/runs")
async def list_runs(limit: int = 20, session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(
        select(TrainingRun).order_by(TrainingRun.created_at.desc()).limit(max(1, min(int(limit), 100)))
    )).scalars().all()
    return {"runs": [_run_json(r) for r in rows]}
