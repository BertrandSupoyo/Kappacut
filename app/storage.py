"""The only place media filesystem paths are built.

Layout under settings.media_root:
  users/<user_id>/projects/<project_id>/{source.<ext>, clips/, frames/, waveform.png}
  sfx/_builtin/<name>.m4a
  sfx/users/<user_id>/<file>
Every path is checked to stay inside media_root (traversal guard).
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.config import settings

MEDIA = settings.media_root.resolve()


def _inside(p: Path) -> Path:
    rp = p.resolve()
    if rp != MEDIA and MEDIA not in rp.parents:
        raise ValueError(f"path escapes media root: {p}")
    return rp


def project_dir(user_id: uuid.UUID, project_id: uuid.UUID, *, create: bool = True) -> Path:
    d = _inside(MEDIA / "users" / str(user_id) / "projects" / str(project_id))
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def clips_dir(user_id: uuid.UUID, project_id: uuid.UUID) -> Path:
    d = _inside(project_dir(user_id, project_id) / "clips")
    d.mkdir(parents=True, exist_ok=True)
    return d


def frames_dir(user_id: uuid.UUID, project_id: uuid.UUID) -> Path:
    d = _inside(project_dir(user_id, project_id) / "frames")
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_path(user_id: uuid.UUID, project_id: uuid.UUID, ext: str) -> Path:
    ext = ext if ext.startswith(".") else f".{ext}" if ext else ""
    return _inside(project_dir(user_id, project_id) / f"source{ext}")


def find_source(user_id: uuid.UUID, project_id: uuid.UUID) -> Path | None:
    d = project_dir(user_id, project_id, create=False)
    if not d.is_dir():
        return None
    return next((p for p in d.iterdir() if p.stem == "source"), None)


def waveform_path(user_id: uuid.UUID, project_id: uuid.UUID) -> Path:
    return _inside(project_dir(user_id, project_id) / "waveform.png")


def thumbnails_dir(user_id: uuid.UUID, project_id: uuid.UUID) -> Path:
    d = _inside(project_dir(user_id, project_id) / "thumbnails")
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumbnail_path(user_id: uuid.UUID, project_id: uuid.UUID, clip_index: int) -> Path:
    return _inside(thumbnails_dir(user_id, project_id) / f"{int(clip_index)}.png")


def clip_path(user_id: uuid.UUID, project_id: uuid.UUID, name: str) -> Path:
    if Path(name).name != name:
        raise ValueError("bad clip name")
    return _inside(clips_dir(user_id, project_id) / name)


def delete_project_media(user_id: uuid.UUID, project_id: uuid.UUID) -> None:
    d = project_dir(user_id, project_id, create=False)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


def builtin_sfx_dir() -> Path:
    d = _inside(MEDIA / "sfx" / "_builtin")
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_sfx_dir(user_id: uuid.UUID) -> Path:
    d = _inside(MEDIA / "sfx" / "users" / str(user_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sfx_path(user_id: uuid.UUID | None, filename: str) -> Path:
    if Path(filename).name != filename:
        raise ValueError("bad sfx name")
    base = builtin_sfx_dir() if user_id is None else user_sfx_dir(user_id)
    return _inside(base / filename)


def dir_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
