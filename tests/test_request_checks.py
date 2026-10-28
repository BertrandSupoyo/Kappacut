"""Request-side checks that keep junk away from the worker: the render payload rebuild
(app/projects.py _validate_ranges) and the admin quota override parser.

Same convention as the other test files: plain `test_*` functions, bare asserts, no pytest
import, so it runs as `python tests/test_request_checks.py` (CI) and under `pytest tests/`.
Nothing here touches the database, Redis or the bucket.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for k, v in {"S3_ENDPOINT_URL": "http://minio:9000", "S3_BUCKET": "kappacut-test",
             "S3_ACCESS_KEY_ID": "testkey", "S3_SECRET_ACCESS_KEY": "testsecret"}.items():
    os.environ.setdefault(k, v)

from fastapi import HTTPException  # noqa: E402

from app.admin import _override  # noqa: E402
from app.config import settings  # noqa: E402
from app.projects import MAX_SFX_PER_CLIP, _validate_ranges  # noqa: E402


def _refused(raw) -> str:
    try:
        _validate_ranges(raw)
    except HTTPException as e:
        assert e.status_code == 400
        return str(e.detail)
    raise AssertionError(f"accepted {raw!r}")


# what editor/store.ts keptRanges() sends for a fully dressed clip
EDITOR_RANGE = {
    "start": 12.5, "end": 41.0, "vertical": True, "label": "Pokémon rage quit",
    "crop": {"mode": "cropfill", "px": 0.4, "py": 0.42, "zoom": 1.2, "blur": 24,
             "track": [[12.5, 0.4, 0.5], [20.0, 0.6, 0.5]]},
    "captions": {"style": "bold", "position": "bottom", "size": 1.2, "anim": "pop", "karaoke": True},
    "color": {"preset": "punchy", "b": 0.1, "c": 1.1, "s": 1.2},
    "sfx": [{"file": "whoosh.m4a", "at": 0, "gain": -3}, {"file": "impact.m4a", "at": 4.2, "gain": -4}],
}


def test_what_the_editor_sends_comes_through_unchanged():
    out = _validate_ranges([EDITOR_RANGE])[0]
    assert out == EDITOR_RANGE, json.dumps(out)


def test_numbers_are_clamped_to_what_the_editor_offers():
    r = {**EDITOR_RANGE,
         "captions": {**EDITOR_RANGE["captions"], "size": 100, "style": "comic-sans", "anim": "spin"},
         "crop": {**EDITOR_RANGE["crop"], "zoom": 50, "blur": "lots", "px": float("nan")},
         "color": {"preset": "x" * 100, "b": 9, "c": -1, "s": float("inf")},
         "sfx": [{"file": "boom.m4a", "at": 999, "gain": 1e9}]}
    out = _validate_ranges([r])[0]
    assert out["captions"]["size"] == 2.5 and out["captions"]["style"] == "clean"
    assert out["captions"]["anim"] == "none"
    assert out["crop"]["zoom"] == 4 and out["crop"]["blur"] == 24 and out["crop"]["px"] == 0.5
    assert len(out["color"]["preset"]) == 32 and out["color"]["b"] == 0.4
    assert out["color"]["c"] == 0.5 and out["color"]["s"] == 1
    assert out["sfx"] == [{"file": "boom.m4a", "at": 41.0 - 12.5, "gain": 12}]


def test_unknown_fields_are_dropped():
    r = {**EDITOR_RANGE, "evil": 1, "crop": {**EDITOR_RANGE["crop"], "x": "1);drawtext"}}
    out = _validate_ranges([r])[0]
    assert "evil" not in out and "x" not in out["crop"]


def test_impossible_bounds_are_refused():
    assert "start" in _refused([{"start": "soon", "end": 5}])
    assert "start < end" in _refused([{"start": 5, "end": 5}])
    assert "start < end" in _refused([{"start": float("nan"), "end": 5}])
    assert "start < end" in _refused([{"start": 0, "end": float("inf")}])


def test_a_clip_longer_than_the_cap_is_refused():
    msg = _refused([{"start": 0, "end": settings.render_max_clip_seconds + 1}])
    assert "at most" in msg, msg
    assert _validate_ranges([{"start": 0, "end": settings.render_max_clip_seconds}])


def test_lists_inside_a_range_are_capped():
    r = {**EDITOR_RANGE, "sfx": [{"file": "a.m4a", "at": 1}] * 500,
         "crop": {**EDITOR_RANGE["crop"], "track": [[1, 0.5, 0.5]] * 5000}}
    out = _validate_ranges([r])[0]
    assert len(out["sfx"]) == MAX_SFX_PER_CLIP and len(out["crop"]["track"]) == 240


def test_off_switches_stay_off():
    out = _validate_ranges([{"start": 0, "end": 10, "vertical": False, "crop": {"mode": "fill"},
                             "captions": {}, "color": None, "sfx": "nope"}])[0]
    assert out["crop"] is None and out["captions"] is None and out["color"] is None
    assert out["sfx"] == []


def test_admin_quota_override():
    assert _override("", "minutes") is None and _override(0, "minutes") is None
    assert _override(" 120 ", "minutes") == 120
    for bad in ("lots", "1.5", -5, 2**40):
        try:
            _override(bad, "minutes")
        except HTTPException as e:
            assert e.status_code == 400
        else:
            raise AssertionError(f"accepted {bad!r}")


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
