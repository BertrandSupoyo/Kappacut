"""app/train_weights.py - learning the score weights, and refusing to claim a win it can't show.

Same convention as the other tests: plain functions, bare asserts, runs as
`python tests/test_train_weights.py` or under pytest. Pure: no database, no AI calls.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kappacut as cf  # noqa: E402
from app.train_weights import (  # noqa: E402
    HAND,
    MIN_VIDEOS,
    PARTS,
    Video,
    features,
    fit,
    format_result,
    load_videos,
    pair_accuracy,
    project_pairs,
    result_json,
    train,
)

PART_VALUES = {"hook": 0.8, "story": 0.6, "energy": 0.5, "opening": 0.7, "clarity": 0.9, "pacing": 0.4}


def _clip(a=0, b=20, arc=None, **parts):
    return {"start_seconds": a, "end_seconds": b, "arc": arc or {}, "breakdown": {**PART_VALUES, **parts}}


def _product_score(clip, energy_weight=1.0):
    """What score_breakdown adds up, from the features the trainer sees."""
    w = dict(cf.SCORE_WEIGHTS, energy=cf.SCORE_WEIGHTS["energy"] * energy_weight)
    return float(np.array([cf.SCORE_WEIGHTS[k] for k in PARTS]) @ features(clip, energy_weight)) * 100 / sum(w.values())


def _videos(truth, n, seed=0, noise=0.03, picks=6):
    """Clips ranked by a known set of weights plus some human inconsistency; the lowest are bad."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        parts = rng.random((picks, len(PARTS)))
        order = np.argsort(-(parts @ truth + rng.normal(0, noise, picks)))
        cut = picks - int(rng.integers(1, 4))
        pairs = np.array([parts[g] - parts[b] for g in order[:cut] for b in order[cut:]])
        out.append(Video(f"video {i}", pairs, True))
    return out


def _coin_flip_videos(n, seed=0, picks=6):
    """Labels with nothing behind them: no weights can genuinely predict these."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        parts = rng.random((picks, len(PARTS)))
        order = rng.permutation(picks)
        out.append(Video(f"video {i}", np.array([parts[g] - parts[b] for g in order[:4] for b in order[4:]]), True))
    return out


LOUD_TRUTH = np.array([0.02, 0.02, 0.80, 0.02, 0.02, 0.12])     # far from the hand-set weights


def test_features_add_up_to_the_score_the_product_shows():
    plain = _clip()
    expected = sum(cf.SCORE_WEIGHTS[k] * PART_VALUES[k] for k in PARTS)
    assert abs(_product_score(plain) - expected) < 1e-6


def test_features_match_score_breakdown_itself():
    # a real Clip through the real scorer, judge verdict and category weight included
    segs = [{"start": 0.0, "end": 20.0, "text": "word " * 50, "conf": -0.4}]
    loud = [-20.0 + (i % 7) for i in range(60)]
    for arc in ({"hook": 2, "arc": 2, "standalone": 1, "quote": 1, "arousal": 1},
                {"hook": 0, "arc": 0, "standalone": 0, "quote": 0, "arousal": 0}):
        for ew in (1.0, 0.3, 1.4):
            c = cf.Clip(start_seconds=0.0, end_seconds=20.0, title="t", hook="h", quote="q", why="w",
                        score=5.0, arc=arc)
            sb = cf.score_breakdown(c, segs, loud, ew)
            total = sb.pop("total")
            mine = _product_score({"arc": arc, "breakdown": sb}, ew)
            assert abs(mine - total) <= 1.5, (arc, ew, mine, total)     # stored parts are rounded


def test_judge_gate_shrinks_the_measured_parts_only():
    weak = dict(hook=0.1, story=0.1)
    rated, unrated = features(_clip(arc={"hook": 0}, **weak)), features(_clip(**weak))
    judged = [PARTS.index(k) for k in cf.JUDGED_PARTS]
    measured = [i for i in range(len(PARTS)) if i not in judged]
    assert (rated[judged] == unrated[judged]).all()
    assert (rated[measured] < unrated[measured]).all()


def test_category_energy_weight_scales_energy_only():
    a, b = features(_clip(), 1.0), features(_clip(), 0.3)
    i = PARTS.index("energy")
    assert abs(b[i] - a[i] * 0.3) < 1e-9
    assert np.delete(a, i).tolist() == np.delete(b, i).tolist()


def test_older_analyses_without_every_part_are_left_out():
    old = {"start_seconds": 0, "end_seconds": 20, "breakdown": {"hook": 1, "story": 1}}
    assert features(old) is None and features({"start_seconds": 0, "end_seconds": 20}) is None
    assert project_pairs([old, _clip(30, 50)], edits={"1": {"keep": False}}).shape == (0, len(PARTS))


def test_keep_and_skip_make_pairs_and_untouched_videos_make_none():
    clips = [_clip(0, 20, hook=0.9), _clip(30, 50, hook=0.5), _clip(60, 80, hook=0.1)]
    assert len(project_pairs(clips)) == 0                               # nobody said no: no signal
    assert len(project_pairs(clips, edits={"1": {"start": 31.0}})) == 0  # a trim isn't a skip
    pairs = project_pairs(clips, edits={"2": {"keep": False}})
    assert len(pairs) == 2 and (pairs[:, PARTS.index("hook")] > 0).all()


def test_hand_labels_win_over_keep_and_skip():
    clips = [_clip(0, 20, hook=0.9), _clip(30, 50, hook=0.1)]
    pairs = project_pairs(clips, moments=[(32.0, 45.0)], edits={"1": {"keep": False}})
    assert len(pairs) == 1 and pairs[0, PARTS.index("hook")] < 0        # the label says clip 1 is the good one


def test_a_label_with_no_good_moments_gives_no_pairs():
    assert len(project_pairs([_clip(0, 20), _clip(30, 50)], moments=[])) == 0


def test_training_learns_weights_it_was_not_given():
    w = fit([v.pairs for v in _videos(LOUD_TRUTH, 30)])
    assert w[PARTS.index("energy")] > HAND[PARTS.index("energy")] + 0.15
    assert abs(w.sum() - 1) < 1e-9 and (w > 0).all()


def test_one_video_barely_moves_the_weights():
    # a little data must not throw away the hand-set weights, even when it all points one way
    one = np.abs(fit([_videos(LOUD_TRUTH, 1)[0].pairs]) - HAND).max()
    all_agree = np.array([[0, 0, 1.0, 0, 0, 0]])                         # a clip won on energy alone
    assert one < 0.10 and np.abs(fit([all_agree]) - HAND).max() < 0.10
    many = np.abs(fit([v.pairs for v in _videos(LOUD_TRUTH, 40)]) - HAND).max()
    assert many > 0.25                                                   # ...while plenty of videos can


def test_a_video_with_many_pairs_does_not_outvote_the_others():
    hook, energy = PARTS.index("hook"), PARTS.index("energy")
    for_hook = np.zeros(len(PARTS))
    for_hook[hook], for_hook[energy] = 0.1, -0.1
    big = np.tile(for_hook, (500, 1))                                    # one video, 500 pairs for hook
    small = [-for_hook[None, :] for _ in range(9)]                       # nine videos, 1 pair each for energy
    by_video = fit([big, *small])
    assert by_video[energy] > by_video[hook]
    by_pair = fit([np.vstack([big, *small])])                            # the mistake this guards against
    assert by_pair[hook] > by_pair[energy]


def test_pair_accuracy_counts_ties_as_half():
    w = np.array([1.0, 0, 0, 0, 0, 0])
    pairs = np.zeros((4, 6))
    pairs[0, 0], pairs[1, 0] = 1, -1                                     # right, wrong, tie, tie
    assert pair_accuracy(w, pairs) == 0.5


def test_real_signal_wins_on_videos_it_never_saw():
    r = train(_videos(LOUD_TRUTH, 20))
    assert r.wins and r.learned_accuracy > r.hand_accuracy + 0.02 and r.better > r.worse
    assert "SCORE_WEIGHTS = {" in format_result(r)


def test_coin_flip_labels_do_not_win():
    r = train(_coin_flip_videos(20))
    assert not r.wins and "keep the current weights" in format_result(r)


def test_nothing_to_gain_when_the_hand_weights_are_already_right():
    r = train(_videos(HAND, 20))
    assert not r.wins


def test_too_few_videos_never_win_even_with_real_signal():
    r = train(_videos(LOUD_TRUTH, MIN_VIDEOS - 1))
    assert not r.wins and "Too few videos" in format_result(r)
    assert "SCORE_WEIGHTS = {" not in format_result(r)


def test_no_data_explains_what_to_do():
    empty = Video("nobody skipped", np.empty((0, len(PARTS))))
    r = train([empty])
    assert r.videos == 0 and not r.wins and "Nothing to train on" in format_result(r)


def test_one_video_reports_weights_but_no_held_out_score():
    r = train(_videos(LOUD_TRUTH, 1))
    assert r.hand_accuracy is None and not r.wins and set(r.weights) == set(PARTS)
    assert "Too few videos" in format_result(r)


def test_a_result_can_be_stored_as_json():
    # numpy booleans and floats in the result would fail at the database, after the training ran
    for r in (train(_videos(LOUD_TRUTH, 12)), train(_coin_flip_videos(3)), train(_videos(LOUD_TRUTH, 1)), train([])):
        stored = json.loads(json.dumps(result_json(r)))
        assert isinstance(stored["wins"], bool) and stored["verdict"]
        assert (stored["paste"] is not None) == stored["wins"]


class _Row:
    """Stands in for a Project: load_videos only reads these fields."""

    def __init__(self, pid, size, name="a video"):
        self.id, self.source_bytes, self.source_filename, self.name = pid, size, name, name


class _Session:
    def __init__(self, rows, edits):
        self.rows, self.edits = rows, edits

    async def execute(self, _query):
        rows = self.rows

        class Result:
            def all(self):
                return rows

        return Result()

    async def get(self, _model, pid):
        state = self.edits.get(pid)

        class Edit:
            pass

        if state is None:
            return None
        e = Edit()
        e.state = {"edits": state}
        return e


def _project(pid, size, skip=True):
    clips = [_clip(0, 20, hook=0.9), _clip(30, 50, hook=0.2)]
    return (_Row(pid, size), clips, None), ({"1": {"keep": False}} if skip else {})


def test_uploads_of_one_video_stay_on_one_side_of_the_held_out_test():
    a, a_edit = _project("aaaaaaaa", 111)
    again, again_edit = _project("bbbbbbbb", 111)      # the same file, uploaded again
    other, other_edit = _project("cccccccc", 222)
    session = _Session([a, again, other], {"aaaaaaaa": a_edit, "bbbbbbbb": again_edit, "cccccccc": other_edit})
    videos = asyncio.run(load_videos(session, {}))
    assert sorted(len(v.pairs) for v in videos) == [1, 2]


def test_load_videos_skips_unreviewed_projects_and_prefers_labels():
    reviewed, reviewed_edit = _project("aaaaaaaa", 111)
    untouched, _ = _project("bbbbbbbb", 222)
    labelled, _ = _project("cccccccc", 333)
    session = _Session([reviewed, untouched, labelled], {"aaaaaaaa": reviewed_edit})
    videos = asyncio.run(load_videos(session, {"cccccccc": [(30.0, 50.0)]}))
    assert len(videos) == 2 and [v.hand_labelled for v in videos] == [False, True]
    assert videos[0].pairs[0, PARTS.index("hook")] > 0 and videos[1].pairs[0, PARTS.index("hook")] < 0


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
