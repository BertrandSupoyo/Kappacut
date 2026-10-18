"""app/evaluate.py - scoring the AI's picks against what the user did with them.

Same convention as the other tests: plain functions, bare asserts, runs as
`python tests/test_evaluate.py` or under pytest. Pure: no database.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluate import evaluate_project  # noqa: E402


def _clip(a, b, score=50, crop=None):
    c = {"start_seconds": a, "end_seconds": b, "virality": score}
    if crop:
        c["auto"] = {"crop": crop}
    return c


def test_untouched_clips_count_as_kept_and_unmoved():
    p = evaluate_project([_clip(0, 20), _clip(30, 50)], {}, [])
    assert p["kept"] == 2 and p["skipped"] == 0 and p["trimmed"] == 0 and p["mean_abs_shift"] == 0


def test_skips_trims_and_direction_of_moves():
    clips = [_clip(0, 20), _clip(30, 50), _clip(60, 80)]
    edits = {"0": {"keep": False}, "1": {"start": 27.0, "end": 50.5}}     # skip one, pull one earlier
    p = evaluate_project(clips, edits, [])
    assert p["skipped"] == 1 and p["kept"] == 2
    assert p["trimmed"] == 1                                              # 0.5 s end move is under the threshold
    assert p["mean_start_shift"] < 0                                      # the user wanted it to start earlier


def test_renders_are_matched_to_picks_by_overlap():
    clips = [_clip(0, 20), _clip(30, 50)]
    p = evaluate_project(clips, {}, [(2.0, 18.0), (49.0, 70.0)])         # covers clip 0; barely touches clip 1
    assert p["rendered"] == 1


def test_reframing_away_from_the_suggestion_is_detected():
    crop = {"mode": "crop", "px": 0.5, "py": 0.5, "zoom": 1, "follow": True}
    clips = [_clip(0, 20, crop=crop), _clip(30, 50, crop=crop)]
    edits = {"0": {"look": {"mode": "crop", "px": 0.5, "py": 0.5, "zoom": 1, "follow": True}},
             "1": {"look": {"mode": "crop", "px": 0.9, "py": 0.5, "zoom": 1, "follow": False}}}
    assert evaluate_project(clips, edits, [])["reframed"] == 1


def test_score_ranking_is_checked_against_keep_and_skip():
    clips = [_clip(0, 20, 90), _clip(30, 50, 80), _clip(60, 80, 30)]
    good = evaluate_project(clips, {"2": {"keep": False}}, [])            # skipped the lowest-scored
    bad = evaluate_project(clips, {"0": {"keep": False}}, [])             # skipped the highest-scored
    assert good["pairs"] == 2 and good["pairs_right"] == 2
    assert bad["pairs_right"] == 0


def test_older_analyses_fall_back_to_the_1_to_10_score():
    old = [{"start_seconds": 0, "end_seconds": 20, "score": 8.0}, {"start_seconds": 30, "end_seconds": 50, "score": 3.0}]
    p = evaluate_project(old, {"1": {"keep": False}}, [])
    assert p["score_kept"] == 80 and p["score_skipped"] == 30


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
