"""Sound-effect library — builtin (shared) + per-user uploads."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.auth import current_verified_user
from app.db import get_session
from app.models.sfx import SfxAsset
from app.models.user import User
from app.sfx_synth import BUILTIN_NAMES, synth_builtins

router = APIRouter(tags=["sfx"])

SFX_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"}
SFX_MAX_BYTES = 8 << 20


async def _ensure_builtins(session: AsyncSession) -> None:
    await run_in_threadpool(synth_builtins)
    have = {
        r.filename for r in (
            await session.execute(select(SfxAsset).where(SfxAsset.user_id.is_(None)))
        ).scalars()
    }
    d = storage.builtin_sfx_dir()
    added = False
    for name in BUILTIN_NAMES:
        fn = f"{name}.m4a"
        if fn in have:
            continue
        f = d / fn
        if f.is_file():
            session.add(SfxAsset(user_id=None, filename=fn, name=name,
                                 bytes=f.stat().st_size))
            added = True
    if added:
        await session.commit()


@router.get("/api/sfx")
async def sfx_library(
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _ensure_builtins(session)
    rows = (await session.execute(
        select(SfxAsset).where(or_(SfxAsset.user_id.is_(None), SfxAsset.user_id == user.id))
        .order_by(SfxAsset.user_id.is_(None).desc(), SfxAsset.name)
    )).scalars().all()
    return {"sfx": [{
        "id": str(r.id), "file": r.filename, "name": r.name,
        "builtin": r.user_id is None,
        "url": f"/media/sfx/{r.id}",
    } for r in rows]}


@router.post("/api/sfx")
async def sfx_upload(
    file: UploadFile,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    import clipfinder as cf
    ext = Path(file.filename or "sfx.mp3").suffix.lower() or ".mp3"
    if ext not in SFX_EXTS:
        raise HTTPException(400, f"audio only ({', '.join(sorted(SFX_EXTS))})")
    stem = cf.slugify(Path(file.filename or "sfx").stem) or "sfx"
    d = storage.user_sfx_dir(user.id)
    dest = d / f"{stem}{ext}"
    n = 1
    while dest.exists():
        dest = d / f"{stem}-{n}{ext}"
        n += 1

    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > SFX_MAX_BYTES:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file too large (8 MB max)")
            fh.write(chunk)

    asset = SfxAsset(user_id=user.id, filename=dest.name,
                     name=dest.stem.replace("-", " "), bytes=size)
    session.add(asset)
    await session.commit()
    return {"id": str(asset.id), "file": asset.filename, "name": asset.name,
            "builtin": False, "url": f"/media/sfx/{asset.id}"}


@router.get("/media/sfx/{asset_id}")
async def media_sfx(
    asset_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
):
    asset = await session.get(SfxAsset, asset_id)
    if asset is None or (asset.user_id is not None and asset.user_id != user.id):
        raise HTTPException(404, "no such sfx")
    p = storage.sfx_path(asset.user_id, asset.filename)
    if not p.is_file():
        raise HTTPException(404, "sfx file missing")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})
