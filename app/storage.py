"""The only place media object keys are built, and the only module that talks to S3.

Layout in the bucket — unchanged from the local volume it replaces, so the one-shot
migration is a straight copy:

  users/<user_id>/projects/<project_id>/source.<ext>
  users/<user_id>/projects/<project_id>/clips/<name>.mp4
  users/<user_id>/projects/<project_id>/frames/<sec>.jpg
  users/<user_id>/projects/<project_id>/thumbnails/<clip_index>.png
  users/<user_id>/projects/<project_id>/waveform.png
  sfx/_builtin/<name>.m4a
  sfx/users/<user_id>/<file>
  uploads/                       tusd's own prefix — it writes multipart objects here

Two rules this module exists to enforce:

1. **A key is built here or not at all.** It is a '/'-joined list of segments that each
   passed `_seg()`, never a caller-supplied string. A bucket has no directories and no
   `..` semantics, so a bad segment does not "escape" the way it would on a filesystem —
   it silently addresses *someone else's* prefix, which is worse than an error.
2. **Nothing outside this module holds a local media path.** ffmpeg needs a seekable
   local file, so callers that run it ask `download()` into a temp dir they own and clean
   up. That is the only way bytes touch disk.

Every function here is synchronous (boto3). The worker calls them directly — it is
already running in `asyncio.to_thread`. Request handlers wrap the ones that do I/O in
`run_in_threadpool`, the same idiom the codebase already uses for ffmpeg. `presign_get()`
is the exception: it signs locally and makes no network call, so it is safe to call
straight from async code.
"""
from __future__ import annotations

import functools
import re
import uuid
from pathlib import Path
from typing import Iterable, Iterator

import boto3
from botocore.client import Config

from app.config import settings

# Deliberately a whitelist. Everything that reaches a key is either a UUID, a slug from
# `kappacut.slugify` ([\w-]), an integer, or a fixed literal — all of which fit. The
# first character may not be `.` or `-`, which is what rejects `.`, `..` and dotfiles;
# `_` is allowed there because the shared `_builtin` prefix uses it and an underscore
# carries no traversal meaning.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")

USERS_PREFIX = "users"
SFX_PREFIX = "sfx"
UPLOADS_PREFIX = "uploads"


def _seg(value: object) -> str:
    """Validate one segment of a key."""
    s = str(value)
    if not _SEGMENT_RE.match(s) or ".." in s:
        raise ValueError(f"unsafe key segment: {value!r}")
    return s


def _key(*segments: object) -> str:
    return "/".join(_seg(s) for s in segments)


@functools.lru_cache(maxsize=1)
def client():
    """One shared boto3 client. Clients are thread-safe for ordinary operations, which is
    what lets the threadpool wrappers and the worker share this instance."""
    return _make_client(settings.s3_endpoint_url)


@functools.lru_cache(maxsize=1)
def _presign_client():
    """Signs browser-facing URLs against the public endpoint. SigV4 covers the Host, so
    the URL has to be signed for the host the browser will actually send."""
    if not settings.s3_public_endpoint_url:
        return client()
    return _make_client(settings.s3_public_endpoint_url)


def _make_client(endpoint_url: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        region_name=settings.s3_region or "auto",
        # R2 (and every current S3 implementation) wants SigV4; virtual-host addressing
        # breaks against custom endpoints, so pin path style.
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            # boto3 >= 1.36 attaches CRC32 checksums by default. Files over the 8 MB
            # multipart threshold then fail at CompleteMultipartUpload on S3-compatible
            # stores (s3mock: "must include the checksum for each part"; R2 has had the
            # same trouble) — every rendered clip longer than a few seconds was lost.
            # Only checksum when an operation actually requires it, as boto3 used to.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def _bucket() -> str:
    if not settings.s3_bucket:
        raise RuntimeError("S3_BUCKET is not configured")
    return settings.s3_bucket


# --------------------------------------------------------------- key builders

def project_prefix(user_id: uuid.UUID, project_id: uuid.UUID) -> str:
    return _key(USERS_PREFIX, user_id, "projects", project_id) + "/"


def source_key(user_id: uuid.UUID, project_id: uuid.UUID, ext: str) -> str:
    ext = ext if ext.startswith(".") else f".{ext}" if ext else ""
    return _key(USERS_PREFIX, user_id, "projects", project_id, f"source{ext}")


def clip_key(user_id: uuid.UUID, project_id: uuid.UUID, name: str) -> str:
    if Path(name).name != name:
        raise ValueError("bad clip name")
    return _key(USERS_PREFIX, user_id, "projects", project_id, "clips", name)


def frame_key(user_id: uuid.UUID, project_id: uuid.UUID, second: int) -> str:
    return _key(USERS_PREFIX, user_id, "projects", project_id, "frames", f"{int(second)}.jpg")


def thumbnail_key(user_id: uuid.UUID, project_id: uuid.UUID, clip_index: int) -> str:
    return _key(USERS_PREFIX, user_id, "projects", project_id, "thumbnails",
                f"{int(clip_index)}.png")


def waveform_key(user_id: uuid.UUID, project_id: uuid.UUID) -> str:
    return _key(USERS_PREFIX, user_id, "projects", project_id, "waveform.png")


def builtin_sfx_key(filename: str) -> str:
    if Path(filename).name != filename:
        raise ValueError("bad sfx name")
    return _key(SFX_PREFIX, "_builtin", filename)


def user_sfx_key(user_id: uuid.UUID, filename: str) -> str:
    if Path(filename).name != filename:
        raise ValueError("bad sfx name")
    return _key(SFX_PREFIX, "users", user_id, filename)


def sfx_key(user_id: uuid.UUID | None, filename: str) -> str:
    """Builtin sfx (shared, `user_id is None`) or one of a user's own uploads."""
    return builtin_sfx_key(filename) if user_id is None else user_sfx_key(user_id, filename)


def is_upload_key(key: str) -> bool:
    """True if `key` is something tusd wrote, and therefore something post-finish may adopt.

    tusd names its objects itself, so this is a prefix check rather than a `_seg()` one —
    but it is the same job the old `_staged_path()` did: refuse to import an object the
    caller merely *claims* is a finished upload.
    """
    return (
        isinstance(key, str)
        and key.startswith(UPLOADS_PREFIX + "/")
        and ".." not in key
        and not key.startswith("/")
        and len(key) > len(UPLOADS_PREFIX) + 1
    )


# --------------------------------------------------------------- object operations

def put_bytes(key: str, data: bytes, content_type: str | None = None) -> int:
    extra = {"ContentType": content_type} if content_type else {}
    client().put_object(Bucket=_bucket(), Key=key, Body=data, **extra)
    return len(data)


def put_file(key: str, path: Path, content_type: str | None = None) -> int:
    extra = {"ContentType": content_type} if content_type else {}
    client().upload_file(str(path), _bucket(), key, ExtraArgs=extra or None)
    return path.stat().st_size


def download(key: str, dest: Path) -> Path:
    """Fetch an object to a local file the caller owns (and is responsible for removing)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    client().download_file(_bucket(), key, str(dest))
    return dest


def copy(src_key: str, dest_key: str) -> None:
    """Server-side copy — no bytes pass through this process."""
    client().copy_object(
        Bucket=_bucket(), Key=dest_key, CopySource={"Bucket": _bucket(), "Key": src_key}
    )


def head(key: str) -> dict | None:
    """Object metadata, or None when it does not exist."""
    try:
        return client().head_object(Bucket=_bucket(), Key=key)
    except client().exceptions.ClientError:
        return None


def exists(key: str) -> bool:
    return head(key) is not None


def size_of(key: str) -> int:
    meta = head(key)
    return int(meta["ContentLength"]) if meta else 0


def list_prefix(prefix: str) -> Iterator[dict]:
    paginator = client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        yield from page.get("Contents") or []


def delete_key(key: str) -> None:
    client().delete_object(Bucket=_bucket(), Key=key)


def _delete_keys(keys: Iterable[str]) -> int:
    bucket, n = _bucket(), 0
    batch: list[dict] = []
    for key in keys:
        batch.append({"Key": key})
        if len(batch) == 1000:                      # the API's per-call maximum
            client().delete_objects(Bucket=bucket, Delete={"Objects": batch})
            n += len(batch)
            batch = []
    if batch:
        client().delete_objects(Bucket=bucket, Delete={"Objects": batch})
        n += len(batch)
    return n


def delete_prefix(prefix: str) -> int:
    """Delete everything under `prefix`. Used for project deletion and retention GC."""
    return _delete_keys(obj["Key"] for obj in list_prefix(prefix))


def presign_get(key: str, *, ttl: int | None = None, filename: str | None = None) -> str:
    """A short-lived URL a browser can fetch directly. Signs locally — no network call, so
    this is safe to call from async code. The caller MUST have checked ownership first."""
    params: dict = {"Bucket": _bucket(), "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{_seg(filename)}"'
    return _presign_client().generate_presigned_url(
        "get_object", Params=params, ExpiresIn=ttl or settings.presign_ttl_seconds
    )


def presign_internal(key: str, ttl: int = 3600) -> str:
    """A URL for the worker's own ffmpeg, signed for the internal endpoint (not the public
    one presign_get uses). ffmpeg seeks it with range requests, so a per-clip render reads
    only the part of the source it cuts instead of downloading all of it."""
    return client().generate_presigned_url(
        "get_object", Params={"Bucket": _bucket(), "Key": key}, ExpiresIn=ttl
    )


# --------------------------------------------------------------- project helpers

def find_source(user_id: uuid.UUID, project_id: uuid.UUID) -> str | None:
    """The project's source object key, whatever extension it landed with."""
    prefix = project_prefix(user_id, project_id)
    for obj in list_prefix(prefix):
        name = obj["Key"][len(prefix):]
        if "/" not in name and name.startswith("source."):
            return obj["Key"]
    return None


def clear_stale_media(user_id: uuid.UUID, project_id: uuid.UUID, keep_source: str) -> int:
    """After a new source lands on a project that already had one: drop any other
    `source.<ext>` (find_source could otherwise pick the old file) and what was derived
    from the old video - the waveform and frames, which are only built when missing, so
    they would have shown the old video forever. Rendered clips and thumbnails stay."""
    prefix = project_prefix(user_id, project_id)
    stale = []
    for obj in list_prefix(prefix):
        key, name = obj["Key"], obj["Key"][len(prefix):]
        if key != keep_source and (("/" not in name and name.startswith("source."))
                                   or name == "waveform.png" or name.startswith("frames/")):
            stale.append(key)
    return _delete_keys(stale)


def delete_project_media(user_id: uuid.UUID, project_id: uuid.UUID) -> int:
    return delete_prefix(project_prefix(user_id, project_id))


def _user_prefixes(user_id: uuid.UUID) -> tuple[str, str]:
    """Where a user's objects live: all their projects, and their uploaded sfx."""
    return _key(USERS_PREFIX, user_id) + "/", _key(SFX_PREFIX, "users", user_id) + "/"


def delete_user_media(user_id: uuid.UUID) -> int:
    return sum(delete_prefix(p) for p in _user_prefixes(user_id))


def export_links(user_id: uuid.UUID, ttl: int) -> list[dict]:
    """Signed links to the files a user would want a copy of. Frames and waveforms are left
    out: they're small images rebuilt from the source, and a project can have hundreds."""
    out = []
    for prefix in _user_prefixes(user_id):
        for obj in list_prefix(prefix):
            key = obj["Key"]
            if "/frames/" in key or key.endswith("/waveform.png"):
                continue
            out.append({"file": key[len(prefix):], "bytes": int(obj.get("Size") or 0),
                        "url": presign_get(key, ttl=ttl)})
    return out
