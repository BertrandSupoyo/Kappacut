"""The state of what the app depends on, for /api/health and Admin > Overview."""
from __future__ import annotations

import shutil
import tempfile

import redis.asyncio as aioredis
from sqlalchemy import text

from app.config import settings
from app.db import engine

WORKER_KEY = "kappacut:worker:"     # + worker id; each worker refreshes its own (app.worker._heartbeat)


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)


async def check_system() -> dict:
    db_ok = redis_ok = False
    queue_depth = 0
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            queue_depth = int(
                (await conn.execute(
                    text("SELECT count(*) FROM jobs WHERE status in ('queued','running')")
                )).scalar_one()
            )
        db_ok = True
    except Exception:
        pass
    try:
        r = _redis()
        redis_ok = bool(await r.ping())
        await r.aclose()
    except Exception:
        pass

    disk_free_pct = 100.0
    try:
        # scratch space, not a media volume — see quota.assert_disk_ok
        du = shutil.disk_usage(tempfile.gettempdir())
        disk_free_pct = round(du.free / du.total * 100, 1)
    except Exception:
        pass

    disk_ok = disk_free_pct >= settings.disk_min_free_pct
    return {
        "ok": db_ok and redis_ok and disk_ok,
        "db": db_ok,
        "redis": redis_ok,
        "disk_free_pct": disk_free_pct,
        "disk_ok": disk_ok,
        "queue_depth": queue_depth,
    }


async def count_workers() -> int | None:
    """Workers with a live heartbeat; None if Redis can't say. Not part of the public /api/health,
    which anyone can read."""
    try:
        r = _redis()
        try:
            return sum([1 async for _ in r.scan_iter(match=f"{WORKER_KEY}*", count=100)])
        finally:
            await r.aclose()
    except Exception:
        return None
