"""Account-level read: the user's own usage against their caps."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_verified_user
from app.config import settings
from app.db import get_session
from app.models.user import User
from app.quota import (
    minutes_cap,
    monthly_minutes,
    project_count,
    renders_today,
    storage_bytes,
    storage_cap_bytes,
)

router = APIRouter(prefix="/api/users/me", tags=["account"])


@router.get("/usage")
async def my_usage(
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {
        "plan": "free",
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
