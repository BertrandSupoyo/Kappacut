"""FastAPI application entrypoint.

  uvicorn app.main:app --host 0.0.0.0 --port 8000

Phases 1-3: config + async DB + Redis + health; cookie auth (fastapi-users); per-user
projects / jobs / editor state / renders / media, all scoped to the verified user.
Phase 4 moves job execution to an arq worker; Phase 7 replaces web/ with a built SPA.
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.account import router as account_router
from app.admin import router as admin_router
from app.auth.routes import install_auth
from app.config import settings
from app.credits import CreditError
from app.csrf import CSRFMiddleware
from app.db import engine
from app.projects import media_router, router as projects_router
from app.quota import QuotaError
from app.ratelimit import GlobalRateLimitMiddleware
from app.sfx import router as sfx_router
from app.uploads import router as uploads_router


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    import os

    import clipfinder as cf

    settings.check_production_ready()   # dev secrets / insecure cookies never reach a public deploy
    settings.media_root.mkdir(parents=True, exist_ok=True)
    # tusd runs as a non-root uid; give it a writable staging dir inside the shared volume
    staging = settings.media_root / "_uploads"
    staging.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(staging, 0o777)
    # same cap as the worker, for the on-demand waveform/frame ffmpeg calls this process makes
    cf.FFMPEG_THREADS = settings.ffmpeg_threads
    yield
    await engine.dispose()


app = FastAPI(title="clipfinder", version="0.3.0", lifespan=lifespan)
app.add_middleware(CSRFMiddleware)
app.add_middleware(GlobalRateLimitMiddleware, limit=settings.global_rate_limit_per_min)


@app.exception_handler(QuotaError)
async def _quota_handler(_: Request, exc: QuotaError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"You've hit your {exc.limit} limit.",
            "limit": exc.limit,
            "used": exc.used,
            "cap": exc.cap,
            "resets_at": exc.resets_at.isoformat() if exc.resets_at else None,
        },
    )


@app.exception_handler(CreditError)
async def _credit_handler(_: Request, exc: CreditError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"You've used all your {exc.feature} credits for today.",
            "feature": exc.feature,
            "used": exc.used,
            "cap": exc.cap,
            "resets_at": exc.resets_at.isoformat() if exc.resets_at else None,
        },
    )


@app.get("/api/health")
async def health() -> JSONResponse:
    import shutil

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
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url)
        redis_ok = bool(await r.ping())
        await r.aclose()
    except Exception:
        pass

    disk_free_pct = 100.0
    try:
        du = shutil.disk_usage(settings.media_root)
        disk_free_pct = round(du.free / du.total * 100, 1)
    except Exception:
        pass

    disk_ok = disk_free_pct >= settings.disk_min_free_pct
    ok = db_ok and redis_ok and disk_ok
    return JSONResponse(
        {
            "ok": ok,
            "db": db_ok,
            "redis": redis_ok,
            "disk_free_pct": disk_free_pct,
            "disk_ok": disk_ok,
            "queue_depth": queue_depth,
        },
        status_code=200 if ok else 503,
    )


install_auth(app)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(projects_router)
app.include_router(media_router)
app.include_router(sfx_router)
app.include_router(uploads_router)

# --- built SPA (prod) — Caddy proxies everything here; dev uses the Vite server ---
_WEB = settings.web_dir
if (_WEB / "index.html").is_file():
    if (_WEB / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(_WEB / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "media/", "files/")):
            raise HTTPException(404)
        candidate = _WEB / full_path
        if full_path and candidate.is_file() and _WEB in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(_WEB / "index.html")
