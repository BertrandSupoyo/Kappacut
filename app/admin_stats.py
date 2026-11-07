"""The numbers on Admin > Overview, and how a job is described to an admin.

like_pattern, job_outcome, fill_days and attention_items are the pure parts (tests/test_admin.py).
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import quote

from sqlalchemy import Date, case, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.health import check_system, count_workers
from app.models.job import Job, RenderOutput
from app.models.launch import Takedown
from app.models.project import Project
from app.models.sfx import SfxAsset
from app.models.usage import UsageEvent
from app.models.user import User

DAYS = 14                     # how far back the Overview's charts go
QUEUE_WAIT_WARN = 300         # seconds a job can wait to start before an admin should look
REPEAT_INFRINGER = 2          # copyright removals before the Overview flags an account
_LIVE = ("queued", "running")
_LEVELS = {"critical": 0, "warning": 1, "info": 2}


def like_pattern(term: str) -> str:
    """A search box's text as a LIKE pattern: its % and _ are ordinary characters."""
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def job_outcome(status: str, detail: str | None) -> str:
    """Splits the `error` status in two. `failed` has a raw error behind it: something went wrong.
    `stopped` ended on purpose (an admin's Cancel, a refused video, a removed project), so counting
    it as a failure would hide real problems among the ones nobody needs to fix."""
    if status != "error":
        return status
    return "failed" if detail else "stopped"


def job_json(j: Job, email: str, project: str | None) -> dict:
    return {
        "id": str(j.id), "kind": j.kind, "status": j.status, "outcome": job_outcome(j.status, j.error_detail),
        "pct": j.progress_pct, "msg": j.progress_msg or j.stage, "error": j.error, "detail": j.error_detail,
        "user": email, "project": project, "project_id": str(j.project_id) if j.project_id else None,
        "worker": j.worker_id,
        "created_at": j.created_at.isoformat(),
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


def fill_days(today: dt.date, done: dict, failed: dict, minutes: dict, signups: dict, days: int = DAYS) -> list[dict]:
    """One entry per day, oldest first. A quiet day still gets an entry, or the chart would close
    the gap and make two weeks look like one."""
    out = []
    for back in range(days - 1, -1, -1):
        d = today - dt.timedelta(days=back)
        out.append({"date": d.isoformat(), "done": done.get(d, 0), "failed": failed.get(d, 0),
                    "minutes": round(minutes.get(d, 0.0), 1), "signups": signups.get(d, 0)})
    return out


def _span(seconds: float) -> str:
    mins = round(seconds / 60)
    if mins < 60:
        return f"{mins} minute{'' if mins == 1 else 's'}"
    hours = round(seconds / 3600, 1)
    return f"{hours:g} hour{'' if hours == 1 else 's'}"


def attention_items(
    *,
    system: dict,
    workers: int | None,
    oldest_queued_s: float | None,
    longest_running_s: float | None,
    failed_24h: int,
    repeat: list[tuple[str, int]],
    legal_reviewed: bool,
    disk_min_free_pct: float,
    job_timeout_s: int,
) -> list[dict]:
    """What an admin should look at, worst first. Empty means nothing needs them."""
    items: list[dict] = []

    def add(level: str, text: str, to: str | None = None) -> None:
        items.append({"level": level, "text": text, "to": to})

    if not system["redis"]:
        add("critical", "Redis isn't answering, so new jobs can't start.")
    elif workers == 0:
        add("critical", "No worker is running, so jobs will wait in the queue until one starts.", "/admin/jobs?status=active")
    if not system["disk_ok"]:
        add("critical", f"The server has only {system['disk_free_pct']:g}% of its disk free. "
                        f"Uploads are refused below {disk_min_free_pct:g}%.")
    if oldest_queued_s is not None and oldest_queued_s >= QUEUE_WAIT_WARN:
        add("warning", f"A job has been waiting {_span(oldest_queued_s)} to start.", "/admin/jobs?status=active")
    if longest_running_s is not None and longest_running_s > job_timeout_s:
        add("warning", f"A job has been running for {_span(longest_running_s)}. It may be stuck.", "/admin/jobs?status=active")
    if failed_24h:
        add("warning", f"{failed_24h} job{'' if failed_24h == 1 else 's'} failed in the last 24 hours.", "/admin/jobs?status=failed")
    for email, count in repeat:
        add("warning", f"{email} has had {count} copyright removals. Your Terms say repeat infringers' accounts get closed.",
            f"/admin/users?q={quote(email)}")
    if not legal_reviewed:
        add("info", "The legal pages still show a draft banner. Set LEGAL_REVIEWED=true once a lawyer has checked them.")
    return sorted(items, key=lambda i: _LEVELS[i["level"]])


def _utc_day(column):
    # the zone is a literal so GROUP BY sees the same expression as SELECT; as a bound parameter
    # each would get its own placeholder, and Postgres refuses the query
    return cast(func.timezone(literal_column("'UTC'"), column), Date)


async def build_overview(session: AsyncSession) -> dict:
    now = dt.datetime.now(dt.UTC)
    today = now.date()
    week_ago = now - dt.timedelta(days=7)
    day_ago = now - dt.timedelta(days=1)
    first_day = dt.datetime.combine(today - dt.timedelta(days=DAYS - 1), dt.time.min, tzinfo=dt.UTC)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month = (month - dt.timedelta(days=1)).replace(day=1)

    system, workers = await check_system(), await count_workers()

    # ---- the queue, now
    queue = (await session.execute(select(
        func.count().filter(Job.status == "running"),
        func.count().filter(Job.status == "queued"),
        func.min(Job.created_at).filter(Job.status == "queued"),
        func.min(Job.started_at).filter(Job.status == "running"),
    ))).one()
    running, queued, oldest_queued, longest_running = queue

    # ---- jobs that ended
    has_detail = Job.error_detail.isnot(None)
    ended = (await session.execute(select(
        func.count().filter(Job.status == "done", Job.finished_at >= week_ago),
        func.count().filter(Job.status == "error", has_detail, Job.finished_at >= week_ago),
        func.count().filter(Job.status == "error", has_detail, Job.finished_at >= day_ago),
    ))).one()
    done_7d, failed_7d, failed_24h = ended

    day = _utc_day(Job.finished_at)
    done_by_day: dict = {}
    failed_by_day: dict = {}
    for d, status, detailed, n in (await session.execute(
        select(day, Job.status, has_detail, func.count())
        .where(Job.finished_at >= first_day, Job.status.in_(("done", "error")))
        .group_by(day, Job.status, has_detail)
    )).all():
        if status == "done":
            done_by_day[d] = done_by_day.get(d, 0) + n
        elif detailed:
            failed_by_day[d] = failed_by_day.get(d, 0) + n

    # ---- people
    people = (await session.execute(select(
        func.count(),
        func.count().filter(User.created_at >= week_ago),
        func.count().filter(User.is_verified.is_(False)),
        func.count().filter(User.is_active.is_(False)),
    ).select_from(User))).one()
    users_total, users_new, users_unverified, users_suspended = people
    users_active = await session.scalar(
        select(func.count(func.distinct(Job.user_id))).where(Job.created_at >= week_ago))

    day = _utc_day(User.created_at)
    signups = dict((await session.execute(
        select(day, func.count()).where(User.created_at >= first_day).group_by(day))).all())

    # ---- usage. SUM(bigint) comes back as a Decimal, so everything here goes through float or int
    this_month = case((UsageEvent.created_at >= month, UsageEvent.quantity), else_=0)
    past_month = case((UsageEvent.created_at < month, UsageEvent.quantity), else_=0)
    usage = {kind: (float(a or 0), float(b or 0)) for kind, a, b in (await session.execute(
        select(UsageEvent.kind, func.sum(this_month), func.sum(past_month))
        .where(UsageEvent.created_at >= last_month, UsageEvent.kind.in_(("transcribe_seconds", "render_clip")))
        .group_by(UsageEvent.kind)
    )).all()}
    seconds, seconds_before = usage.get("transcribe_seconds", (0.0, 0.0))
    clips, clips_before = usage.get("render_clip", (0.0, 0.0))

    day = _utc_day(UsageEvent.created_at)
    minutes_by_day = {d: float(s or 0) / 60 for d, s in (await session.execute(
        select(day, func.sum(UsageEvent.quantity))
        .where(UsageEvent.created_at >= first_day, UsageEvent.kind == "transcribe_seconds")
        .group_by(day)
    )).all()}

    stored = (
        int(await session.scalar(select(func.coalesce(func.sum(Project.source_bytes), 0))) or 0)
        + int(await session.scalar(select(func.coalesce(func.sum(RenderOutput.bytes), 0))) or 0)
        + int(await session.scalar(select(func.coalesce(func.sum(SfxAsset.bytes), 0)).where(SfxAsset.user_id.isnot(None))) or 0)
    )
    projects = await session.scalar(select(func.count()).select_from(Project))

    # ---- lists
    named = select(Job, User.email, Project.name).join(User, User.id == Job.user_id).outerjoin(Project, Project.id == Job.project_id)
    live_rows = (await session.execute(named.where(Job.status.in_(_LIVE)).order_by(Job.created_at).limit(6))).all()
    failure_rows = (await session.execute(
        named.where(Job.status == "error", has_detail).order_by(Job.finished_at.desc().nulls_last()).limit(5)
    )).all()
    repeat = [(email, n) for email, n in (await session.execute(
        select(User.email, func.count(Takedown.id)).join(Takedown, Takedown.user_id == User.id)
        .where(User.is_active.is_(True)).group_by(User.email)
        .having(func.count(Takedown.id) >= REPEAT_INFRINGER).order_by(func.count(Takedown.id).desc()).limit(5)
    )).all()]

    return {
        "system": {**system, "workers": workers, "worker_concurrency": settings.worker_concurrency},
        "attention": attention_items(
            system=system, workers=workers,
            oldest_queued_s=(now - oldest_queued).total_seconds() if oldest_queued else None,
            longest_running_s=(now - longest_running).total_seconds() if longest_running else None,
            failed_24h=failed_24h, repeat=repeat, legal_reviewed=settings.legal_reviewed,
            disk_min_free_pct=settings.disk_min_free_pct, job_timeout_s=settings.job_timeout_seconds,
        ),
        "queue": {"running": running, "queued": queued},
        "jobs_7d": {"done": done_7d, "failed": failed_7d},
        "users": {"total": users_total, "new_7d": users_new, "unverified": users_unverified,
                  "suspended": users_suspended, "active_7d": users_active or 0},
        "usage": {"minutes_month": round(seconds / 60, 1), "minutes_last_month": round(seconds_before / 60, 1),
                  "clips_month": int(clips), "clips_last_month": int(clips_before)},
        "storage_bytes": stored,
        "projects": projects or 0,
        "days": fill_days(today, done_by_day, failed_by_day, minutes_by_day, signups),
        "live": [job_json(*row) for row in live_rows],
        "failures": [job_json(*row) for row in failure_rows],
    }
