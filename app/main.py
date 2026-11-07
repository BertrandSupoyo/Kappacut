"""FastAPI application entrypoint.

  uvicorn app.main:app --host 0.0.0.0 --port 8000

Phases 1-3: config + async DB + Redis + health; cookie auth (fastapi-users); per-user
projects / jobs / editor state / renders / media, all scoped to the verified user.
Phase 4 moves job execution to an arq worker; Phase 7 replaces web/ with a built SPA.
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.account import router as account_router
from app.admin import router as admin_router
from app.agent import page_router as agent_page_router, router as agent_router
from app.auth.routes import install_auth
from app.config import settings
from app.credits import CreditError
from app.csrf import CSRFMiddleware
from app.db import engine
from app.health import check_system
from app.launch import router as launch_router
from app.projects import media_router, router as projects_router
from app.quota import QuotaError
from app.ratelimit import GlobalRateLimitMiddleware
from app.sfx import router as sfx_router
from app.training import router as training_router
from app.uploads import router as uploads_router

# uvicorn only sets up its own loggers, so the app's info lines went nowhere in this process. Only
# ours: the root logger stays quiet, or every request's URL would be logged by the HTTP clients.
_app_log = logging.getLogger("kappacut")
_app_log.setLevel(logging.INFO)
_app_log.addHandler(logging.StreamHandler())


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    import kappacut as cf

    settings.check_production_ready()   # dev secrets / insecure cookies never reach a public deploy
    # No media staging dir any more: tusd writes straight to the bucket and this process
    # only ever touches temp files (see app/storage.py).
    # same cap as the worker, for the on-demand waveform/frame ffmpeg calls this process makes
    cf.FFMPEG_THREADS = settings.ffmpeg_threads
    yield
    await engine.dispose()


app = FastAPI(title="Kappacut", version="0.3.0", lifespan=lifespan)
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
    status = await check_system()
    return JSONResponse(status, status_code=200 if status["ok"] else 503)


install_auth(app)
app.include_router(launch_router)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(training_router)
app.include_router(agent_router)
app.include_router(agent_page_router)
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
        # index.html names the hashed bundle. Sent with no Cache-Control, browsers cache it
        # heuristically and keep running the previous build after a deploy — so revalidate
        # every time (cheap: ETag → 304). The hashed /assets files stay cacheable.
        return FileResponse(_WEB / "index.html", headers={"Cache-Control": "no-cache"})
