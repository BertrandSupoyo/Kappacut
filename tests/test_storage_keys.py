"""Key building, guards and presigning for app/storage.py.

All of this is pure-local — key building is string work and presigning signs offline — so
these run with no bucket, no network and no credentials beyond the dummies set below.

pytest-compatible (plain `test_*` functions, bare asserts) but deliberately free of a
pytest import, so it also runs as `python tests/test_storage_keys.py`. That is how CI
invokes it today; `pytest tests/` will collect it unchanged.

What these protect: a bucket has no directories and no `..` semantics, so a bad key
segment does not error the way a bad path would — it silently addresses a *different
user's* prefix. Every case below is one way that could happen.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("S3_ENDPOINT_URL", "http://minio:9000")
os.environ.setdefault("S3_BUCKET", "clipfinder-test")
os.environ.setdefault("S3_ACCESS_KEY_ID", "testkey")
os.environ.setdefault("S3_SECRET_ACCESS_KEY", "testsecret")
os.environ.setdefault("S3_REGION", "auto")

from app import storage  # noqa: E402

U = uuid.UUID("11111111-1111-1111-1111-111111111111")
P = uuid.UUID("22222222-2222-2222-2222-222222222222")
OTHER = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _rejects(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError:
        return
    raise AssertionError(f"{fn.__name__}{args!r} was accepted; expected ValueError")


# --------------------------------------------------------------- layout

def test_key_layout_matches_the_volume_it_replaced():
    """The bucket layout is identical to the old local volume, which is what makes
    `app.cli migrate-media` a straight copy. Changing these breaks that."""
    assert storage.source_key(U, P, ".mp4") == f"users/{U}/projects/{P}/source.mp4"
    assert storage.source_key(U, P, "mkv") == f"users/{U}/projects/{P}/source.mkv"
    assert storage.clip_key(U, P, "01_a-slug_vertical_cc.mp4") == \
        f"users/{U}/projects/{P}/clips/01_a-slug_vertical_cc.mp4"
    assert storage.frame_key(U, P, 42) == f"users/{U}/projects/{P}/frames/42.jpg"
    assert storage.thumbnail_key(U, P, 3) == f"users/{U}/projects/{P}/thumbnails/3.png"
    assert storage.waveform_key(U, P) == f"users/{U}/projects/{P}/waveform.png"
    assert storage.builtin_sfx_key("whoosh.m4a") == "sfx/_builtin/whoosh.m4a"
    assert storage.user_sfx_key(U, "boom.mp3") == f"sfx/users/{U}/boom.mp3"
    assert storage.sfx_key(None, "pop.m4a") == "sfx/_builtin/pop.m4a"
    assert storage.project_prefix(U, P) == f"users/{U}/projects/{P}/"


# --------------------------------------------------------------- guards

def test_clip_names_that_would_escape_the_prefix_are_rejected():
    for bad in ("../../etc/passwd", "/etc/passwd", "..", ".", "a/b.mp4", "a\\b.mp4",
                ".hidden", "", "a\x00b.mp4", "a\nb.mp4", "my clip.mp4", "clip⁄x.mp4"):
        _rejects(storage.clip_key, U, P, bad)


def test_sfx_names_that_would_escape_the_prefix_are_rejected():
    for bad in ("../../../secret", "../users/x/y.m4a", "a/b.m4a", "..", ""):
        _rejects(storage.user_sfx_key, U, bad)
        _rejects(storage.builtin_sfx_key, bad)


def test_a_crafted_extension_cannot_escape_the_project_prefix():
    _rejects(storage.source_key, U, P, "./../x")
    _rejects(storage.source_key, U, P, "/../../x")


def test_underscore_is_allowed_as_a_first_character_but_dot_is_not():
    # `_builtin` needs a leading underscore; `.`/`..` must stay rejected.
    assert storage.builtin_sfx_key("_x.m4a").endswith("/_x.m4a")
    _rejects(storage.clip_key, U, P, ".x.mp4")
    _rejects(storage.clip_key, U, P, "-x.mp4")


def test_one_users_key_never_addresses_another_users_prefix():
    assert storage.project_prefix(U, P) != storage.project_prefix(OTHER, P)
    assert str(OTHER) not in storage.clip_key(U, P, "x.mp4")


# --------------------------------------------------------------- upload adoption

def test_is_upload_key_accepts_only_what_tusd_wrote():
    assert storage.is_upload_key("uploads/14b1c4c77771671a8479bc0444bbc5ce")


def test_is_upload_key_refuses_anything_else():
    """post-finish adopts this object as a project's source, so a caller that can reach the
    hook must not be able to name an arbitrary object."""
    for bad in (f"users/{OTHER}/projects/{P}/source.mp4",   # another user's video
                "uploads/../users/x/y",                      # traversal out of the prefix
                "uploads/",                                  # the bare prefix
                "uploadsX/abc",                              # prefix lookalike
                "/uploads/abc",                              # absolute
                "", None, 42):
        assert not storage.is_upload_key(bad), f"accepted {bad!r}"


# --------------------------------------------------------------- presigning

def test_presigned_url_is_scoped_signed_and_expiring():
    url = storage.presign_get(storage.clip_key(U, P, "01_x.mp4"), ttl=300)
    parsed, qs = urlparse(url), parse_qs(urlparse(url).query)
    assert parsed.netloc == "minio:9000"
    assert parsed.path.startswith("/clipfinder-test/")        # path-style addressing
    assert f"users/{U}/projects/{P}/clips/01_x.mp4" in parsed.path
    assert qs["X-Amz-Algorithm"][0] == "AWS4-HMAC-SHA256"
    assert qs["X-Amz-Expires"][0] == "300"
    assert qs.get("X-Amz-Signature")


def test_presigned_url_never_contains_the_secret_key():
    url = storage.presign_get(storage.waveform_key(U, P))
    assert "testsecret" not in url


def test_download_filename_is_validated_not_interpolated():
    # would otherwise break out of the Content-Disposition header
    _rejects(storage.presign_get, storage.clip_key(U, P, "x.mp4"), filename='"; rm -rf /')


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
