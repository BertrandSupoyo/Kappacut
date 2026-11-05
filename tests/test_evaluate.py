"""app/evaluate.py - scoring the AI's picks against what the user did with them.

Same convention as the other tests: plain functions, bare asserts, runs as
`python tests/test_evaluate.py` or under pytest. Pure: no database.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluate import (  # noqa: E402
    MomentReport,
    evaluate_moments,
    evaluate_project,
    format_moment_report,
    label_is_current,
    label_spans,
    matches_moment,
)


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


def test_a_pick_matches_a_moment_when_they_share_half_of_the_shorter():
    assert matches_moment(100, 130, 110, 120)                            # contains it
    assert matches_moment(110, 120, 100, 130)                            # sits inside it
    assert matches_moment(100, 120, 110, 130)                            # half of each
    assert not matches_moment(100, 120, 118, 138)                        # only brushes it
    assert not matches_moment(100, 120, 200, 220)


def test_found_and_good_measure_different_failures():
    clips = [_clip(100, 120, 90), _clip(105, 125, 80), _clip(300, 320, 70)]
    p = evaluate_moments(clips, [(100.0, 125.0), (500.0, 520.0)])
    assert p["moments"] == 2 and p["found"] == 1                         # two picks on one moment count once
    assert p["picks"] == 3 and p["good"] == 2                            # ...but both are good picks
    assert p["pairs"] == 2 and p["pairs_right"] == 2


def test_ranking_against_moments_catches_a_bad_pick_scored_high():
    clips = [_clip(100, 120, 40), _clip(300, 320, 90)]
    p = evaluate_moments(clips, [(100.0, 120.0)])
    assert p["pairs"] == 1 and p["pairs_right"] == 0


def test_a_video_with_no_good_moments_scores_every_pick_bad():
    p = evaluate_moments([_clip(0, 20)], [])
    assert p["good"] == 0 and p["moments"] == 0 and p["pairs"] == 0


def test_a_label_stops_counting_once_its_video_is_replaced():
    assert label_is_current(1000, 1000)
    assert not label_is_current(1000, 2000)
    assert label_is_current(None, 2000) and label_is_current(1000, None)      # a size never recorded


def test_label_spans_reads_stored_moments():
    assert label_spans([{"start": 1, "end": 2.5, "note": "x"}]) == [(1.0, 2.5)]
    assert label_spans(None) == [] and label_spans([]) == []


def _scored(blind, found=1, moments=2):
    return {"picks": 4, "good": 2, "moments": moments, "found": found, "pairs": 4, "pairs_right": 3, "blind": blind}


def test_moment_totals_can_count_only_blind_labels():
    rep = MomentReport(scored=[("a", _scored(True)), ("b", _scored(False, found=2))])
    assert rep.total()["videos"] == 2 and rep.total()["found"] == 3 and rep.total()["moments"] == 4
    assert rep.total(blind_only=True)["videos"] == 1 and rep.total(blind_only=True)["found"] == 1


def test_report_says_how_many_labels_were_blind_and_flags_a_replaced_video():
    text = format_moment_report(MomentReport(scored=[("a", _scored(True))], stale=["old upload"]))
    assert "1 of 1 videos" in text and "old upload" in text and "not scored" in text
    assert "Admin > Training" in format_moment_report(MomentReport())


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
