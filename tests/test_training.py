"""app/training.py - the rules behind Admin > Training that the database and browser don't check.

Same convention as the other tests: plain functions, bare asserts, runs as
`python tests/test_training.py` or under pytest. Pure: no database, no AI calls.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.training import (  # noqa: E402
    LabelIn,
    MomentIn,
    apply_label,
    clean_moments,
    comparison,
    label_status,
    video_detail,
    video_row,
)

CLIPS = [
    {"start_seconds": 100, "end_seconds": 120, "title": "good one", "virality": 90},
    {"start_seconds": 300, "end_seconds": 320, "title": "bad one", "virality": 70},
]
MOMENTS = [{"start": 100.0, "end": 125.0, "note": "the joke"}, {"start": 500.0, "end": 520.0, "note": ""}]


def _project(size=1000):
    return SimpleNamespace(id="p1", name="A video", duration_seconds=600.0, source_bytes=size)


def _label(done=True, blind=True, revealed=True, size=1000, moments=MOMENTS):
    return SimpleNamespace(done=done, blind=blind, revealed=revealed, source_bytes=size, moments=moments, updated_at=None)


def _refused(moments, duration=600.0):
    try:
        clean_moments([MomentIn(**m) for m in moments], duration)
    except HTTPException as e:
        return e.detail
    return None


def test_moments_are_sorted_trimmed_and_rounded():
    out = clean_moments([MomentIn(start=50, end=60.126, note="  later  "), MomentIn(start=10, end=20)], 600.0)
    assert out == [{"start": 10, "end": 20, "note": ""}, {"start": 50, "end": 60.13, "note": "later"}]


def test_bad_moments_are_refused_with_a_reason():
    assert "at least" in _refused([{"start": 10, "end": 10.5}])
    assert "at least" in _refused([{"start": 20, "end": 10}])                 # backwards
    assert "after the video" in _refused([{"start": 10, "end": 700}])
    assert "overlap" in _refused([{"start": 10, "end": 30}, {"start": 20, "end": 40}])


def test_touching_moments_and_a_frame_past_the_end_are_fine():
    assert _refused([{"start": 10, "end": 20}, {"start": 20, "end": 30}]) is None
    assert _refused([{"start": 590, "end": 600.5}]) is None                   # player clock vs probed length
    assert _refused([{"start": 590, "end": 700}], duration=None) is None      # length unknown


def test_impossible_times_never_reach_the_rules():
    for bad in ({"start": float("nan"), "end": 5}, {"start": 0, "end": float("inf")}, {"start": -1, "end": 5}):
        try:
            MomentIn(**bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    try:
        LabelIn(moments=[{"start": i, "end": i + 1} for i in range(201)])
    except ValueError:
        return
    raise AssertionError("accepted 201 moments")


def _row():
    return SimpleNamespace(moments=[], source_bytes=None, done=False, blind=False, revealed=False)


def test_a_draft_is_not_scored_and_not_blind():
    row = _row()
    apply_label(row, MOMENTS, done=False, blind=True, source_bytes=1000)
    assert not row.done and not row.blind and not row.revealed and row.source_bytes == 1000


def test_finishing_for_the_first_time_can_count_as_blind():
    row = _row()
    apply_label(row, MOMENTS, done=True, blind=True, source_bytes=1000)
    assert row.done and row.blind and row.revealed


def test_finishing_without_the_box_ticked_is_not_blind():
    row = _row()
    apply_label(row, MOMENTS, done=True, blind=False, source_bytes=1000)
    assert row.done and not row.blind and row.revealed


def test_once_the_picks_were_shown_a_video_can_never_be_blind_again():
    row = _row()
    apply_label(row, MOMENTS, done=True, blind=True, source_bytes=1000)
    apply_label(row, MOMENTS, done=False, blind=True, source_bytes=1000)      # reopened to edit
    assert not row.done and not row.blind and row.revealed
    apply_label(row, MOMENTS, done=True, blind=True, source_bytes=1000)       # box ticked again
    assert row.done and not row.blind and row.revealed


def test_status_follows_the_label_and_the_source():
    assert label_status(None, 1000) == "none"
    assert label_status(_label(done=False), 1000) == "draft"
    assert label_status(_label(), 1000) == "done"
    assert label_status(_label(), 2000) == "stale"                            # the file was replaced
    assert label_status(_label(size=None), 2000) == "done"                    # size never known


def test_the_ai_picks_stay_hidden_until_the_label_is_finished():
    for label in (None, _label(done=False), _label(size=999)):                # none, draft, replaced video
        d = video_detail(_project(), CLIPS, "comedy", label)
        r = video_row(_project(), CLIPS, "comedy", label)
        assert d["comparison"] is None and r["score"] is None
        for text in (repr(d), repr(r)):
            assert "good one" not in text and "300" not in text               # no title, no pick time
        assert "picks" not in r


def test_a_finished_label_shows_the_comparison():
    d = video_detail(_project(), CLIPS, "comedy", _label())
    c = d["comparison"]
    assert [p["good"] for p in c["picks"]] == [True, False]
    assert [m["found"] for m in c["moments"]] == [True, False]
    assert c["moments"][0]["note"] == "the joke"
    assert c["stats"]["found"] == 1 and c["stats"]["good"] == 1 and c["stats"]["pairs_right"] == 1


def test_comparison_handles_a_video_with_nothing_marked():
    c = comparison(CLIPS, [])
    assert c["moments"] == [] and not any(p["good"] for p in c["picks"]) and c["stats"]["moments"] == 0


def test_older_analyses_score_from_the_1_to_10_field():
    old = [{"start_seconds": 0, "end_seconds": 20, "score": 7.5}]
    assert comparison(old, [])["picks"][0]["score"] == 75


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
