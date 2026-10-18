"""Generic per-user credit ledger — gates any future paid-API generative feature (image /
video generation) separately from the free-tier quotas in app/quota.py, which only cover
Groq's text/audio calls. No feature spends against this yet (see PLAN-boost.md Phase E);
it exists so the FIRST such feature (Phase F: AI thumbnails) has a ready-made, already
correct place to check before making a paid call, instead of inventing its own.

Usage, mirroring app/quota.py's check_quota:
    await check_credits(session, user, "thumbnail")             # raises CreditError if over cap
    ... make the paid call ...
    session.add(UsageEvent(user_id=user.id, project_id=p.id, kind="credit_thumbnail", quantity=1))
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.usage import UsageEvent
from app.models.user import User


class CreditError(Exception):
    def __init__(self, feature: str, used: int, cap: int, resets_at: dt.datetime | None = None):
        self.feature = feature
        self.used = used
        self.cap = cap
        self.resets_at = resets_at
        super().__init__(f"credit '{feature}' exceeded ({used}/{cap})")


def _day_start() -> dt.datetime:
    now = dt.datetime.now(dt.UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def credits_used_today(session: AsyncSession, user_id, feature: str) -> int:
    return int(await session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.kind == f"credit_{feature}",
            UsageEvent.created_at >= _day_start(),
        )
    ) or 0)


async def check_credits(
    session: AsyncSession, user: User, feature: str, *, cost: int = 1, cap: int | None = None
) -> None:
    """Raise CreditError if spending `cost` credits on `feature` would exceed the daily cap.
    Pass `cap` for a feature-specific limit; omit it to share `credit_daily_cap_default`.
    Callers are responsible for recording the spend (a UsageEvent row, see module docstring)
    after their paid call actually succeeds — this function only checks, never spends."""
    effective_cap = cap if cap is not None else settings.credit_daily_cap_default
    used = await credits_used_today(session, user.id, feature)
    if used + cost > effective_cap:
        raise CreditError(feature, used, effective_cap, resets_at=_day_start() + dt.timedelta(days=1))
