"""Tiny per-IP rate limiter backed by Redis — used as a route dependency.

The plan named slowapi; slowapi's decorator model doesn't compose with the third-party
fastapi-users routers, so sensitive auth routes use this `Depends()`-style limiter instead.
slowapi stays in requirements for the Phase 5 global limit.
"""
from __future__ import annotations

from collections.abc import Callable

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

_redis = aioredis.from_url(settings.redis_url, decode_responses=True)


def rate_limit(bucket: str, limit: int, window_seconds: int) -> Callable:
    async def dependency(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        key = f"rl:{bucket}:{ip}"
        try:
            n = await _redis.incr(key)
            if n == 1:
                await _redis.expire(key, window_seconds)
        except Exception:  # noqa: BLE001 — Redis down must not lock everyone out
            return
        if n > limit:
            ttl = await _redis.ttl(key)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many attempts. Try again in {max(ttl, 1)}s.",
            )

    return dependency


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    """A blunt per-IP ceiling on /api/* (SSE + health exempt). Fail-open."""

    def __init__(self, app, limit: int = 120, window: int = 60):
        super().__init__(app)
        self.limit = limit
        self.window = window

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            path.startswith("/api/")
            and path != "/api/health"
            and not path.endswith("/events")
            and not path.startswith("/api/upload/hooks")
        ):
            ip = request.client.host if request.client else "unknown"
            key = f"rl:global:{ip}"
            try:
                n = await _redis.incr(key)
                if n == 1:
                    await _redis.expire(key, self.window)
                if n > self.limit:
                    return JSONResponse(
                        {"detail": "Slow down — too many requests."}, status_code=429
                    )
            except Exception:  # noqa: BLE001
                pass
        return await call_next(request)
