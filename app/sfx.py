"""Sound-effect library — builtin (shared) + per-user uploads."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
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
    missing = [f"{n}.m4a" for n in BUILTIN_NAMES if f"{n}.m4a" not in have]
    if not missing:
        return
    added = False
    for fn in missing:
        meta = await run_in_threadpool(storage.head, storage.builtin_sfx_key(fn))
        if meta:
            session.add(SfxAsset(user_id=None, filename=fn, name=Path(fn).stem,
                                 bytes=int(meta["ContentLength"])))
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

    # Read first, then name it: the 8 MB cap is small enough to hold in memory, and it
    # means a rejected upload never creates an object to clean up.
    chunks, size = [], 0
    while chunk := await file.read(1 << 20):
        size += len(chunk)
        if size > SFX_MAX_BYTES:
            raise HTTPException(413, "file too large (8 MB max)")
        chunks.append(chunk)
    if not size:
        raise HTTPException(400, "empty file")

    filename = f"{stem}{ext}"
    n = 1
    while await run_in_threadpool(storage.exists, storage.user_sfx_key(user.id, filename)):
        filename = f"{stem}-{n}{ext}"
        n += 1
    await run_in_threadpool(
        storage.put_bytes, storage.user_sfx_key(user.id, filename), b"".join(chunks)
    )

    asset = SfxAsset(user_id=user.id, filename=filename,
                     name=Path(filename).stem.replace("-", " "), bytes=size)
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
    # ownership is settled above; only then is a URL signed
    return RedirectResponse(storage.presign_get(storage.sfx_key(asset.user_id, asset.filename)),
                            status_code=302)
