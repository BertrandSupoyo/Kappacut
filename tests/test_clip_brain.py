"""The clip-picking "brain" and render helpers: lengths, sentence ends, quiet-gap splitting,
the face camera path and its ffmpeg crop expression, the readable score, the verify step's
ranking / flat-score / count handling, colour, captions and the analyze job's glue.

Same convention as test_storage_keys.py: plain `test_*` functions, bare asserts, no pytest
import, so it runs as `python tests/test_clip_brain.py` (CI) and under `pytest tests/`.
No network: the verify step is driven by a scripted model reply, never Groq. The one ffmpeg
test skips itself when ffmpeg isn't installed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GROQ_API_KEY", "test-not-used")           # groq.Groq() needs one to construct
for k, v in {"S3_ENDPOINT_URL": "http://minio:9000", "S3_BUCKET": "kappacut-test",
             "S3_ACCESS_KEY_ID": "testkey", "S3_SECRET_ACCESS_KEY": "testsecret"}.items():
    os.environ.setdefault(k, v)

import kappacut as cf  # noqa: E402
import pipeline  # noqa: E402


def _cfg(**over):
    cfg = dict(cf.DEFAULTS)
    cfg.update({"min_seconds": 15.0, "max_seconds": 45.0, "snap_max_shift": 4.0,
                "lead_seconds": 0.4, "tail_seconds": 0.8, "max_gap_seconds": 6.0,
                "min_words": 5, "backend": "groq", "groq_model": "openai/gpt-oss-20b",
                "groq_verify_model": "", "count": 3, "min_clips": 3, "taste": "", "category": ""})
    cfg.update(over)
    return cfg


def _seg(a, b, text="one two three four five six"):
    return {"start": float(a), "end": float(b), "text": text}


def _clip(a, b, **kw):
    return cf.Clip(start_seconds=a, end_seconds=b, **kw)


# --------------------------------------------------------------- length + cut points

def test_short_clip_grows_forward_to_a_sentence_end():
    segs = [_seg(0, 5), _seg(5, 12), _seg(12, 20)]
    c = _clip(2, 10)                                   # 8 s: under the 15 s minimum
    cf._clamp_clip(c, 100.0, _cfg(), segs)
    assert c.start_seconds == 2                        # setup untouched
    assert c.end_seconds == 20                         # 2+15 lands mid-line, so it finishes the line


def test_short_clip_reaches_back_only_at_the_end_of_the_video():
    c = _clip(90, 95)
    cf._clamp_clip(c, 96.0, _cfg(), [])
    assert c.end_seconds == 96 and abs((c.end_seconds - c.start_seconds) - 15) < 1e-6


def test_long_clip_is_capped_at_max_seconds():
    c = _clip(0, 60)
    cf._clamp_clip(c, 100.0, _cfg(), [])
    assert c.end_seconds == 45


def test_end_inside_a_sentence_finishes_it_when_close():
    c = _clip(0, 20)
    cf._end_on_sentence(c, [_seg(10, 22)], _cfg(), 100.0)
    assert abs(c.end_seconds - 22.3) < 1e-6


def test_end_inside_a_long_sentence_stops_before_it_when_that_keeps_the_minimum():
    c = _clip(0, 25)
    cf._end_on_sentence(c, [_seg(18, 40)], _cfg(), 100.0)
    assert c.end_seconds == 18


def test_end_on_a_pause_is_left_alone():
    c = _clip(0, 20)
    cf._end_on_sentence(c, [_seg(10, 15), _seg(25, 30)], _cfg(), 100.0)
    assert c.end_seconds == 20


def test_degenerate_pick_is_dropped():
    segs = [_seg(0, 30, "a b c d e f g h i j k l m n o p q r s t")]
    out = cf._finalize([_clip(10, 10)], segs, 3, _cfg())
    assert out == []


# --------------------------------------------------------------- topic-aware windows

def _two_topics():
    a = [_seg(t, t + 4, "ramen broth noodles spicy chili tasting kitchen") for t in range(0, 180, 5)]
    b = [_seg(t, t + 4, "football stadium goalkeeper penalty referee supporters") for t in range(185, 420, 5)]
    return a + b


def test_window_cuts_where_the_topic_changes_not_on_the_clock():
    wins = cf._windows(_two_topics(), 240)
    assert abs(wins[0][-1]["end"] - 179) < 0.01            # ends with topic A at 3:00, not 4:00
    assert any(s["start"] == 185 for s in wins[1])         # topic B opens the next window


def test_windows_overlap_and_cover_everything():
    segs = _two_topics()
    wins = cf._windows(segs, 240)
    assert wins[1][0]["start"] < wins[0][-1]["end"]        # the next window repeats the tail
    assert wins[0][-1]["end"] - wins[1][0]["start"] <= cf.WINDOW_OVERLAP + 5
    covered = {id(s) for w in wins for s in w}
    assert covered == {id(s) for s in segs}
    assert all(w[-1]["start"] - w[0]["start"] <= 240 for w in wins)


def test_a_short_video_is_one_window():
    segs = [_seg(t, t + 4) for t in range(0, 120, 5)]
    assert len(cf._windows(segs, 240)) == 1 and cf._windows([], 240) == []


def test_uniform_talk_still_ends_within_the_span():
    segs = [_seg(t, t + 4.5, "same words every line here again") for t in range(0, 900, 5)]
    wins = cf._windows(segs, 240)
    assert len(wins) >= 4 and all(w[-1]["start"] - w[0]["start"] <= 240 for w in wins)


# --------------------------------------------------------------- quiet vs loud gaps

def _loud(quiet_from=10, quiet_to=20, level=-50.0, n=60):
    return [level if quiet_from <= i < quiet_to else -30.0 for i in range(n)]


def test_a_quiet_gap_splits_off_the_other_moment():
    segs = [_seg(0, 10, "one two three four five"), _seg(20, 30, "six seven eight nine ten eleven")]
    c = _clip(0, 30)
    cf._trim_to_dense_block(c, segs, _cfg(), _loud(level=-50.0))
    assert c.start_seconds > 18                        # kept the wordier second block


def test_a_loud_gap_without_words_stays_in_the_clip():
    segs = [_seg(0, 10, "one two three four five"), _seg(20, 30, "six seven eight nine ten eleven")]
    c = _clip(0, 30)
    cf._trim_to_dense_block(c, segs, _cfg(), _loud(level=-20.0))   # chewing / screaming
    assert c.start_seconds == 0 and c.end_seconds == 30


# --------------------------------------------------------------- face camera path

def test_camera_ignores_jitter_inside_the_dead_zone():
    times = [i * 0.5 for i in range(20)]
    xs = [0.50 if i % 2 else 0.53 for i in range(20)]
    path = cf._camera_path(times, xs, [0.4] * 20)
    assert len(path) == 2                              # just the two ends: the camera never moved
    assert all(abs(p[1] - path[0][1]) < 1e-9 for p in path)


def test_camera_follows_a_real_move():
    times = [i * 0.5 for i in range(30)]
    xs = [0.3] * 10 + [0.7] * 20
    path = cf._camera_path(times, xs, [0.4] * 30)
    assert path[0][1] < 0.35 and path[-1][1] > 0.6


def test_camera_fills_missed_detections():
    times = [i * 0.5 for i in range(10)]
    xs = [0.4, None, None, 0.4, 0.4, None, 0.4, 0.4, 0.4, None]
    path = cf._camera_path(times, xs, [0.5 if x is not None else None for x in xs])
    assert all(p[1] is not None and p[2] is not None for p in path)


def test_camera_jumps_on_a_shot_change_instead_of_gliding():
    times = [i * 0.5 + 0.25 for i in range(20)]               # 0.25 … 9.75 s
    xs = [0.3 if t < 5 else 0.7 for t in times]
    path = cf._camera_path(times, xs, [0.4] * 20, cuts=[5.0])
    before = [p for p in path if p[0] < 5.0]
    after = [p for p in path if p[0] >= 5.0]
    assert before[-1][0] >= 4.98 and abs(before[-1][1] - 0.3) < 0.01   # held right up to the cut
    assert after[0][0] == 5.0 and abs(after[0][1] - 0.7) < 0.01         # new framing on the cut frame
    glide = cf._camera_path(times, xs, [0.4] * 20)                       # no cut info: eases across
    assert not any(abs(p[1] - 0.7) < 0.01 and p[0] <= 5.3 for p in glide)


def test_a_cut_between_matching_shots_does_not_jolt():
    times = [i * 0.5 + 0.25 for i in range(20)]
    path = cf._camera_path(times, [0.5] * 10 + [0.52] * 10, [0.4] * 20, cuts=[5.0])
    assert all(abs(p[1] - 0.5) < 0.01 for p in path)


def test_a_faceless_shot_holds_the_usual_position():
    times = [i * 0.5 + 0.25 for i in range(30)]
    xs = [0.6] * 10 + [None] * 10 + [0.6] * 10                # a food close-up in the middle
    path = cf._camera_path(times, xs, [0.4 if v else None for v in xs], cuts=[5.0, 10.0])
    assert all(abs(p[1] - 0.6) < 0.01 for p in path)


def test_fast_cutting_still_renders():
    if not shutil.which(cf.FFMPEG) and not Path(cf.FFMPEG).exists():
        print("    (skipped: ffmpeg not installed)")
        return
    times = [i * 0.5 + 0.25 for i in range(40)]
    cuts = [float(c) for c in range(1, 20)]                   # a cut every second
    xs = [0.25 if int(t) % 2 else 0.75 for t in times]
    track = cf._camera_path(times, xs, [0.5] * 40, cuts=cuts)
    assert len(track) > 30
    x, y = cf._follow_xy({"track": track}, 0.0, 0.5, 0.5)
    p = subprocess.run([cf.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=20",
                        "-vf", f"crop=w=ih*9/16:h=ih:x={x}:y={y}", "-f", "null", "-"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-400:]


def test_track_expression_is_filtergraph_safe():
    expr = cf._track_expr([[10, 0.2, 0.5], [15, 0.5, 0.5], [20, 0.8, 0.5]], 10.0, 1)
    assert expr and "," not in expr.replace("\\,", "")  # every comma escaped for -vf
    assert cf._track_expr([[10, 0.2, 0.5]], 10.0, 1) is None   # one point is not a path


def test_follow_falls_back_to_static_placement_without_a_track():
    x, y = cf._follow_xy({}, 0.0, 0.25, 0.5)
    assert x == "(iw-ow)*0.2500" and y == "(ih-oh)*0.5000"


def test_moving_crop_renders_in_ffmpeg():
    if not shutil.which(cf.FFMPEG) and not Path(cf.FFMPEG).exists():
        print("    (skipped: ffmpeg not installed)")
        return
    x, y = cf._follow_xy({"track": [[0, 0.1, 0.5], [2, 0.9, 0.5]]}, 0.0, 0.5, 0.5)
    p = subprocess.run([cf.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=2",
                        "-vf", f"crop=w=ih*9/16:h=ih:x={x}:y={y}", "-f", "null", "-"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-400:]


def test_frame_on_centres_and_clamps():
    assert cf.frame_on({"x": 0.5, "y": 0.4, "ar": 16 / 9}) == {"px": 0.5, "py": 0.5}
    assert cf.frame_on({"x": 0.95, "y": 0.4, "ar": 16 / 9})["px"] == 1.0   # can't pan past the edge


# --------------------------------------------------------------- readable score

def test_score_is_0_to_100_and_rank_moves_it():
    segs = [_seg(0, 20, " ".join(["word"] * 50))]
    best, worst = _clip(0, 20, score=6), _clip(0, 20, score=6)
    for c in (best, worst):
        c.arc = {"hook": 2, "arc": 2, "quote": 1, "standalone": 1, "trend": 1}
    best._rank_pct, worst._rank_pct = 1.0, 0.0
    b, w = cf.score_breakdown(best, segs, None), cf.score_breakdown(worst, segs, None)
    assert 0 <= w["total"] < b["total"] <= 100
    assert set(b) == {"total", "hook", "story", "energy", "opening", "clarity", "pacing"}


# --------------------------------------------------------------- the opening (hook-first)

def test_filler_first_lines_are_cut_from_the_start():
    segs = [_seg(0, 1.5, "Okay so."), _seg(1.6, 2.4, "Um, alright."),
            _seg(2.5, 9, "This is the worst burger I have ever eaten"), _seg(9, 25, "words " * 30)]
    c = _clip(0, 25)
    cf._trim_weak_open(c, segs, _cfg())
    assert abs(c.start_seconds - (2.5 - 0.4)) < 1e-6, c.start_seconds


def test_a_real_first_line_and_a_reaction_are_left_alone():
    for first in ("So I quit my job yesterday", "Oh!", "Ohhh no"):
        segs = [_seg(0, 2, first), _seg(2, 20, "words " * 30)]
        c = _clip(0, 20)
        cf._trim_weak_open(c, segs, _cfg())
        assert c.start_seconds == 0, (first, c.start_seconds)


def test_filler_is_never_trimmed_down_to_nothing():
    segs = [_seg(0, 1, "Okay."), _seg(1, 2, "Yeah.")]
    c = _clip(0, 2)
    cf._trim_weak_open(c, segs, _cfg())
    assert c.start_seconds < 1.0                       # the last spoken line always stays


def test_dead_air_at_the_start_scores_a_weaker_opening():
    talk = " ".join(["word"] * 60)
    prompt_clip, slow_clip = _clip(0, 20), _clip(0, 20)
    prompt_segs = [_seg(0, 20, talk)]
    slow_segs = [_seg(3.5, 20, talk)]                  # 3.5 s before anyone speaks
    fast = cf.score_breakdown(prompt_clip, prompt_segs, None)
    slow = cf.score_breakdown(slow_clip, slow_segs, None)
    assert fast["opening"] > slow["opening"] + 0.3, (fast, slow)
    assert fast["total"] > slow["total"]


def test_a_loud_opening_beats_a_quiet_one_but_less_for_podcasts():
    segs = [_seg(0, 20, " ".join(["word"] * 60))]
    loud = [-40.0] * 60
    quiet_start, loud_start = list(loud), list(loud)
    for i in range(0, 4):
        loud_start[i] = -18.0
    c = _clip(0, 20)
    hi, lo = cf.score_breakdown(c, segs, loud_start), cf.score_breakdown(c, segs, quiet_start)
    assert hi["opening"] > lo["opening"]
    hi_p, lo_p = (cf.score_breakdown(c, segs, x, energy_weight=0.3) for x in (loud_start, quiet_start))
    assert hi_p["opening"] - lo_p["opening"] < hi["opening"] - lo["opening"]


def test_arousal_raises_story_when_the_judge_rates_it():
    segs = [_seg(0, 20, " ".join(["word"] * 50))]
    calm, hot = _clip(0, 20), _clip(0, 20)
    base = {"hook": 2, "arc": 2, "quote": 1, "standalone": 1, "trend": 1}
    calm.arc, hot.arc = dict(base, arousal=0), dict(base, arousal=2)
    assert cf.score_breakdown(hot, segs, None)["story"] > cf.score_breakdown(calm, segs, None)["story"]


def _good_verdicts(n=6):
    return [{"index": i, "keep": True, "hook": 3 - i % 3, "arc": 2, "quote": 1, "standalone": 2,
             "trend": 1, "arousal": 1, "rank": i + 1, "beat_type": "quote", "hook_type": "claim",
             "hook_line": f"line {i} of the clip", "payoff_line": f"payoff {i} of the clip"} for i in range(n)]


def test_a_verdict_copied_from_the_example_loses_its_lines_and_labels():
    vs = _good_verdicts()
    vs[3].update(hook_line="anyway so my chair broke yesterday",
                 payoff_line="chat is DYING I can't breathe", beat_type="fail", hook_type="story")
    out = _verify(vs, count=6)
    assert not any("chair broke" in c.hook_line or c.beat_type == "fail" for c in out), \
        [(c.hook_line, c.beat_type) for c in out]


def test_a_reply_rejecting_almost_everything_is_retried_then_not_trusted():
    calls = []
    broken = [dict(v, keep=(i == 0), reason="no payoff" if i else "") for i, v in enumerate(_good_verdicts())]

    def judge(*a, **k):
        calls.append(1)
        return _scripted(broken)()
    out = _verify(judge, count=4)
    assert len(calls) == 2, calls                       # asked once more
    assert len(out) >= 3, len(out)                      # mass rejections didn't empty the list


def test_a_broken_first_reply_is_replaced_by_a_good_second_one():
    replies = [_scripted([dict(v, keep=False, reason="x") for v in _good_verdicts()]),
               _scripted(_good_verdicts())]
    out = _verify(lambda *a, **k: replies.pop(0)(), count=4)
    assert not replies and len(out) == 4, len(out)


def test_a_reply_that_echoes_the_example_everywhere_is_not_trusted_to_reject():
    calls = []
    echo = [dict(v, hook_line="anyway so my chair broke yesterday", keep=(i % 2 == 0),
                 reason="" if i % 2 == 0 else "weak") for i, v in enumerate(_good_verdicts())]

    def judge(*a, **k):
        calls.append(1)
        return _scripted(echo)()
    out = _verify(judge, count=6)
    assert len(calls) == 2                              # retried
    assert len(out) == 6, len(out)                      # its rejections were ignored


def test_a_reply_still_broken_after_the_retry_is_not_trusted_for_scores_either():
    # seen live: 5 of 6 rejected with zero scores, twice; keeping its numbers sank good clips
    # under the floor and a 19-minute video came back with 1 clip
    broken = [dict(v, keep=(i == 0), hook=0, arc=0, quote=0, standalone=0, trend=0,
                   reason="" if i == 0 else "no payoff") for i, v in enumerate(_good_verdicts())]
    out = _verify(broken, count=6)
    assert len(out) == 6, len(out)
    assert all(c.arc == {} for c in out)               # stage-1 ranking: none of its scores used


def test_a_placeholder_beat_type_is_not_shown():
    vs = _good_verdicts()
    for v in vs:
        v["beat_type"] = "<reaction|fail|bit|story|quote|hot_take|argument|reveal|wholesome|moment>"
    out = _verify(vs, count=6)
    assert all(c.beat_type == "moment" for c in out), [c.beat_type for c in out]


def test_subscription_talk_is_not_a_call_to_action():
    assert not cf._is_cta("They charged my subscription twice")
    assert cf._is_cta("Don't forget to subscribe")
    assert cf._is_cta("thanks to everyone who subscribed")


def test_verify_keeps_hook_type_and_arousal():
    verdicts = [{"index": i, "keep": True, "hook": 3 - i % 3, "arc": 2, "quote": 1, "standalone": 2,
                 "trend": 1, "arousal": i % 3, "rank": i + 1, "hook_type": "Question",
                 "beat_type": "hot_take"} for i in range(6)]
    out = _verify(verdicts)
    assert out and all(c.hook_type == "question" for c in out), [c.hook_type for c in out]
    assert all("arousal" in c.arc for c in out)


# --------------------------------------------------------------- verify step (scripted model)

def _scripted(verdicts):
    reply = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps({"verdicts": verdicts})), finish_reason="stop")])
    return lambda *a, **k: reply


def _verify(verdicts, n=6, count=3, floor=0):
    segs = [_seg(i * 30, i * 30 + 25, " ".join(["word"] * 40)) for i in range(n)]
    clips = [_clip(i * 30, i * 30 + 20, score=5) for i in range(n)]
    real = cf._groq_chat
    cf._groq_chat = verdicts if callable(verdicts) else _scripted(verdicts)
    try:
        return cf._verify_and_refine(clips, segs, _cfg(count=count, min_clips=count, quality_floor=floor))
    finally:
        cf._groq_chat = real


def test_count_is_a_maximum_weak_clips_are_not_padded_in():
    # two strong clips, four the judge scored at the bottom; asked for 5
    vs = [{"index": i, "keep": True, "hook": 3 if i < 2 else 0, "arc": 3 if i < 2 else 0,
           "quote": 2 if i < 2 else 0, "standalone": 2 if i < 2 else 0, "trend": 2 if i < 2 else 0,
           "rank": i + 1} for i in range(6)]
    out = _verify(vs, count=5, floor=60)
    assert len(out) == 2 and {c.start_seconds for c in out} == {0.0, 30.0}


def test_a_rejected_clip_never_pads_the_list():
    vs = [{"index": i, "keep": i != 1, "hook": 2, "arc": 3 if i != 1 else 1, "quote": 1,
           "standalone": 1, "trend": 1, "rank": i + 1, "reason": "staples two moments" if i == 1 else ""}
          for i in range(3)]
    out = _verify(vs, n=3, count=3)
    assert 30.0 not in {c.start_seconds for c in out}


def test_nothing_clears_the_floor_still_returns_the_best_one():
    vs = [{"index": i, "keep": True, "hook": 0, "arc": 0, "quote": 0, "standalone": 0, "trend": 0,
           "rank": i + 1} for i in range(4)]
    out = _verify(vs, n=4, count=3, floor=99)
    assert len(out) == 1


def test_verify_returns_the_top_ranked_clips_in_time_order():
    vs = [{"index": i, "keep": True, "hook": 2, "arc": 2 + (i % 2), "quote": i % 3, "standalone": 1,
           "trend": 1, "rank": r} for i, r in enumerate([6, 1, 5, 2, 4, 3])]
    out = _verify(vs)
    starts = [c.start_seconds for c in out]
    assert len(out) == 3 and starts == sorted(starts)
    assert {c.start_seconds for c in out} == {30.0, 90.0, 150.0}       # ranks 1, 2, 3


def test_identical_scores_are_ignored_but_the_rank_still_counts():
    vs = [{"index": i, "keep": True, "hook": 1, "arc": 2, "quote": 1, "standalone": 1, "trend": 1,
           "rank": i + 1} for i in range(6)]
    out = _verify(vs)
    assert all(c.arc == {} for c in out)               # flat verdicts carry no information
    assert all(c._rank_pct is not None for c in out)


def test_a_failed_verify_still_respects_the_clip_count():
    def outage(*a, **k):
        raise RuntimeError("simulated outage")
    out = _verify(outage, count=3)
    assert len(out) == 3


def test_verify_prompt_carries_no_example_scores():
    # the old worked example's 2/3/2/2/2 was echoed onto every clip
    assert '"hook":2' not in cf._VERIFY_BRIEF and '"rank"' in cf._VERIFY_SHAPE


# --------------------------------------------------------------- render helpers

def test_brightness_scales_like_the_preview():
    m = cf.color_matrix({"preset": "none", "b": -0.24, "c": 1, "s": 1})
    assert all(abs(m[i][i] - 0.76) < 1e-9 for i in range(3)) and all(row[3] == 0 for row in m)


def test_neutral_look_adds_no_filter():
    with tempfile.TemporaryDirectory() as d:
        assert cf._color_chain({"preset": "none", "b": 0, "c": 1, "s": 1}, Path(d) / "x.cube") == ""
        assert not (Path(d) / "x.cube").exists()


def test_presets_come_from_the_shared_file():
    presets = cf._color_presets()
    assert {"none", "punchy", "warm", "cool", "film", "mono"} <= set(presets)
    cool = cf.color_matrix({"preset": "cool", "b": 0, "c": 1, "s": 1})
    assert cool[2][2] > cool[0][0]                             # cool: more blue gain than red
    mono = cf.color_matrix({"preset": "mono", "b": 0, "c": 1, "s": 1})
    assert abs(mono[0][1] - mono[1][1]) < 1e-9                 # greyscale: every output row the same


def test_render_applies_exactly_the_preview_matrix():
    """Push a known colour through the real lut3d and compare with the matrix maths the
    editor's SVG filter uses. Skips without ffmpeg."""
    if not shutil.which(cf.FFMPEG) and not Path(cf.FFMPEG).exists():
        print("    (skipped: ffmpeg not installed)")
        return
    look = {"preset": "cool", "b": -0.1, "c": 1.2, "s": 1.3}
    m = cf.color_matrix(look)

    def first_pixel(chain):
        p = subprocess.run([cf.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=0x668C4D:s=16x16:d=0.1",
                            "-vf", f"format=rgb24{',' + chain if chain else ''},format=rgb24", "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        assert p.returncode == 0, p.stderr[-300:]
        return list(p.stdout[:3])

    # lavfi's color source is generated in YUV, so read the real input pixel rather than
    # assuming the hex value survived the conversion
    src = [v / 255 for v in first_pixel("")]
    want = [max(0.0, min(1.0, r[0] * src[0] + r[1] * src[1] + r[2] * src[2] + r[3])) * 255 for r in m]
    with tempfile.TemporaryDirectory() as d:
        got = first_pixel(cf._color_chain(look, Path(d) / "g.cube"))
    assert all(abs(g - w) <= 1.5 for g, w in zip(got, want)), (got, [round(w, 1) for w in want])


def test_captions_wrap_inside_the_frame():
    with tempfile.TemporaryDirectory() as d:
        p = pipeline.build_ass([{"start": 0, "end": 2, "text": "a long line " * 10}], "bold", "bottom",
                               1.6, True, Path(d) / "c.ass")
        assert "WrapStyle: 0" in p.read_text(encoding="utf-8")


# --------------------------------------------------------------- category profiles

def test_profile_sets_length_loudness_and_default_taste():
    cfg = _cfg(category="podcast")
    cf.apply_profile(cfg)
    assert (cfg["min_seconds"], cfg["max_seconds"]) == (20.0, 60.0)
    assert cfg["energy_weight"] < 0.5 and "takes" in cfg["taste"]
    assert "complete thought" in cf._steer_line(cfg)


def test_users_own_taste_beats_the_genre_default():
    cfg = _cfg(category="podcast", taste="only the cooking bits", taste_from_user=True)
    cf.apply_profile(cfg)
    assert cfg["taste"] == "only the cooking bits"


def test_unknown_genre_falls_back_to_general():
    cfg = _cfg(category="knitting")
    cf.apply_profile(cfg)
    assert cfg["energy_weight"] == 1.0 and cfg["min_seconds"] == 15.0


def test_food_is_a_known_genre():
    assert "food" in cf.CATEGORIES


def test_energy_weight_changes_how_much_loudness_counts():
    segs = [_seg(0, 20, " ".join(["word"] * 50))]
    quiet_clip = _clip(0, 20, score=8)
    loud = [-50.0] * 30 + [-20.0] * 30                      # the video gets loud later; the clip stays quiet
    calm = cf.score_breakdown(quiet_clip, segs, [-50.0] * 60, 1.0)
    assert calm["energy"] <= 0.5
    low_w = cf.score_breakdown(quiet_clip, segs, loud, 0.3)["total"]
    high_w = cf.score_breakdown(quiet_clip, segs, loud, 1.4)["total"]
    assert low_w > high_w                                    # a quiet clip is punished less in a podcast


# --------------------------------------------------------------- analyze-job glue

def test_project_options_become_engine_settings():
    from app.jobs import _apply_options
    cfg = _apply_options(_cfg(count=6, taste="toml default"), {"count": 3, "content_type": "food",
                                                              "taste": "Prioritize beat_type: fail."})
    assert cfg["count"] == 3 and cfg["count_explicit"] and cfg["category_override"] == "food"
    assert cfg["taste"] == "Prioritize beat_type: fail." and cfg["taste_from_user"]


def test_choosing_only_count_and_type_keeps_the_genre_default_taste():
    from app.jobs import _apply_options
    cfg = _apply_options(_cfg(taste="toml default"), {"count": 3, "content_type": "podcast"})
    assert cfg["taste"] == "toml default" and not cfg.get("taste_from_user")


def test_older_jobs_with_options_in_the_taste_text_still_work():
    from app.jobs import _apply_options
    cfg = _apply_options(_cfg(), {"taste": "Content type: food. 5 clips."})
    assert cfg["count"] == 5 and cfg["category_override"] == "food" and not cfg.get("taste_from_user")


def test_content_type_is_read_from_the_taste_text():
    from app.jobs import _TYPE_RE
    assert _TYPE_RE.search("Content type: food. 3 clips.").group(1) == "food"
    assert _TYPE_RE.search("Prioritize beat_type: fail.") is None

def test_clip_count_is_read_from_the_taste_text():
    from app.jobs import _COUNT_RE
    assert _COUNT_RE.search("Prioritize beat_type: fail. 3 clips. Format: General.").group(1) == "3"
    assert _COUNT_RE.search("10 clips.").group(1) == "10"
    assert _COUNT_RE.search("lots of clips please") is None


def test_auto_render_follows_the_face_only_when_asked():
    from app.jobs import _clip_to_range
    track = [[1.0, 0.4, 0.5], [3.0, 0.6, 0.5]]
    base = {"start_seconds": 1, "end_seconds": 20, "title": "t", "face": {"track": track}}
    on = dict(base, auto={"vertical": True, "crop": {"mode": "crop", "follow": True}})
    off = dict(base, auto={"vertical": True, "crop": {"mode": "crop", "follow": False}})
    assert _clip_to_range(on)["crop"]["track"] == track
    assert "track" not in _clip_to_range(off)["crop"]


# --------------------------------------------------------------- brain fixes A-E

def test_payoff_line_just_after_the_end_is_pulled_in():
    # seen on the Spicy Challenge clip #3: it ended at 162.7, the second its payoff began
    segs = [_seg(141, 150, "tastes like straight tabasco sauce"), _seg(150, 162.7, "you came to see this"),
            _seg(162.7, 177.3, "just like that we have brought the death tsukimen back from the dead")]
    c = _clip(141, 162.7, payoff_line="Just like that we have brought the death Tsukimen back from the dead")
    cf._reach_payoff(c, segs, _cfg(), 400)
    assert c.end_seconds >= 177.3 and c.end_seconds - c.start_seconds <= 45


def test_payoff_is_not_pulled_in_when_the_clip_would_get_too_long():
    segs = [_seg(100, 140, "a long setup that keeps going on and on"),
            _seg(141, 160, "and here is the payoff line at last")]
    c = _clip(100, 140, payoff_line="and here is the payoff line at last")
    cf._reach_payoff(c, segs, _cfg(max_seconds=45.0), 400)
    assert c.end_seconds == 140                        # 60 s would break max_seconds


def test_calls_to_action_are_trimmed_off_the_edges():
    segs = [_seg(53.5, 54.5, "Don't go anywhere."), _seg(54.5, 55.3, "Don't click away."),
            _seg(55.3, 58, "Actually, click the subscribe button."),
            _seg(58, 75, "so we got the ten times fire level tsukimen and it looks unreal"),
            _seg(75, 90, "the gyoza were cooked perfectly but they had one major flaw"),
            _seg(90, 94, "thanks for watching guys")]
    c = _clip(53, 94)
    cf._trim_cta(c, segs, _cfg())
    assert 57 < c.start_seconds <= 58 and 90 <= c.end_seconds < 92


def test_a_clip_the_judge_rated_empty_falls_below_the_floor():
    # clip #2: loud, clear talking (energy/clarity/pacing near 1) that the judge rated arc 0
    segs = [_seg(0, 30, " ".join(["word"] * 80))]
    loud = [-12.0] * 30 + [-20.0] * 60
    empty = _clip(0, 30, score=5.8, arc={"hook": 1, "arc": 0, "quote": 0, "standalone": 1, "trend": 0})
    good = _clip(0, 30, score=8.0, arc={"hook": 2, "arc": 2, "quote": 1, "standalone": 2, "trend": 2})
    e = cf.score_breakdown(empty, segs, loud)["total"]
    g = cf.score_breakdown(good, segs, loud)["total"]
    assert e < 40 <= g, (e, g)


def test_the_gate_leaves_well_rated_clips_alone():
    segs = [_seg(0, 30, " ".join(["word"] * 80))]
    c = _clip(0, 30, score=8.0, arc={"hook": 2, "arc": 2, "quote": 1, "standalone": 2, "trend": 2})
    parts = cf.score_breakdown(c, segs, None)
    plain = sum(cf.SCORE_WEIGHTS[k] * parts[k] for k in cf.SCORE_WEIGHTS) / sum(cf.SCORE_WEIGHTS.values()) * 100
    assert abs(parts["total"] - plain) <= 1


def _challenge_video():
    """Talky first half, then an eating challenge: short reactions between long pauses
    (modelled on the Spicy Challenge transcript, 5:55-7:52)."""
    talk = [_seg(t, t + 9, "and then we headed over to the ramen shop to talk about the menu and the history")
            for t in range(0, 340, 10)]
    react = [_seg(355, 356.7, "The first dunk in there."), _seg(361.6, 365.1, "All right three two one Ohhhh"),
             _seg(371.3, 372.5, "How bad is it?"), _seg(387.1, 390.1, "I can't eat it fast because it's all cold."),
             _seg(405.1, 406.1, "The one in."), _seg(406.1, 407.1, "Get it down."), _seg(407.1, 409.1, "Oh"),
             _seg(409.1, 409.9, "Oh!"), _seg(416.3, 417.2, "Dang."), _seg(418.3, 419.1, "The sweat."),
             _seg(433.8, 435.3, "How much time is it?"), _seg(459.2, 460.2, "Alright, I'm done."),
             _seg(462.2, 463.7, "I think it's nine flat.")]
    return talk + react


def test_the_reaction_finder_proposes_the_challenge():
    segs = _challenge_video()
    found = cf._reaction_candidates(segs, None, _cfg(count=5))
    assert found and all(c._source == "reaction" for c in found)
    assert any(c.start_seconds < 420 and c.end_seconds > 405 for c in found)   # "Oh! … Dang. The sweat."
    assert all(c.start_seconds >= 340 for c in found)                        # not the talking


def test_a_reaction_beat_survives_the_cleanup_rules():
    # the old rules split it at the 6+ s pauses and set it aside as word-sparse
    segs = _challenge_video()
    picker = [_clip(t, t + 25, score=7.0) for t in (0, 100, 200, 300)]
    out = cf._finalize(picker, segs, 5, _cfg(count=5, min_clips=5))
    assert any(c._source == "reaction" and c.start_seconds >= 350 for c in out), \
        [(c.start_seconds, c.end_seconds, c._source) for c in out]


def test_a_reaction_beat_is_not_dropped_for_overlapping_a_weaker_pick():
    # the real failure: two picker clips overlapping the challenge pushed the reaction out
    segs = _challenge_video()
    picker = [_clip(386.7, 407.4, score=6.6), _clip(434.9, 449.9, score=7.6)]
    out = cf._finalize(picker, segs, 5, _cfg(count=5, min_clips=2))
    assert any(c._source == "reaction" and c.start_seconds < 420 < c.end_seconds for c in out), \
        [(c.start_seconds, c.end_seconds, c._source) for c in out]


def test_a_stretched_exclamation_counts_however_long_the_line():
    segs = [_seg(355, 356.7, "The first dunk in there."), _seg(361.6, 365.1, "All right three two one Ohhhh Alright 3 2 1")]
    assert cf._react_weight(segs, 1) == 1.0


def test_an_echoed_placeholder_is_not_a_payoff_line():
    vs = [{"index": i, "keep": True, "hook": 2, "arc": 2, "quote": 1, "standalone": 2, "trend": 1, "rank": i + 1,
           "hook_line": "<first line, verbatim>", "payoff_line": "<payoff line, verbatim>"} for i in range(6)]
    out = _verify(vs, count=3)
    assert out and all(c.hook_line == "" and c.payoff_line == "" for c in out)


def test_an_overlapping_top_up_is_replaced_not_lost():
    # clips 0-1 strong; 2 is the best "thin" one but overlaps clip 0; 3 should take its place
    segs = [_seg(i * 30, i * 30 + 25, " ".join(["word"] * 40)) for i in range(6)]
    clips = [_clip(0, 20, score=5), _clip(30, 50, score=5), _clip(10, 28, score=5),
             _clip(90, 110, score=5), _clip(120, 140, score=5), _clip(150, 170, score=5)]
    vs = [{"index": 0, "keep": True, "hook": 3, "arc": 3, "quote": 2, "standalone": 2, "trend": 2, "rank": 1},
          {"index": 1, "keep": True, "hook": 3, "arc": 3, "quote": 2, "standalone": 2, "trend": 2, "rank": 2},
          {"index": 2, "keep": True, "hook": 3, "arc": 1, "quote": 2, "standalone": 2, "trend": 2, "rank": 3},
          {"index": 3, "keep": True, "hook": 2, "arc": 1, "quote": 1, "standalone": 2, "trend": 1, "rank": 4},
          {"index": 4, "keep": False, "hook": 0, "arc": 0, "quote": 0, "standalone": 0, "trend": 0, "rank": 5, "reason": "broken"},
          {"index": 5, "keep": False, "hook": 0, "arc": 0, "quote": 0, "standalone": 0, "trend": 0, "rank": 6, "reason": "broken"}]
    real = cf._groq_chat
    cf._groq_chat = _scripted(vs)
    try:
        out = cf._verify_and_refine(clips, segs, _cfg(count=3, min_clips=3, quality_floor=40))
    finally:
        cf._groq_chat = real
    starts = sorted(c.start_seconds for c in out)
    assert len(out) == 3 and 10 not in starts and 90 in starts, starts


def test_a_harsh_judge_still_leaves_three_clips_near_the_floor():
    # two clips clear the floor (63, 64); the rest land ~52. Without the soft minimum the
    # list stops at 2; with it, the best near-miss tops it up to 3 - and no further.
    vs = [{"index": i, "keep": True, "hook": 1 + i % 2, "arc": 1, "quote": 0, "standalone": 1,
           "trend": i % 2, "rank": i + 1} for i in range(6)]       # varied, so not the "flat" path
    out = _verify(vs, count=5, floor=60)
    assert len(out) == 3, len(out)


def test_a_rejection_without_a_reason_is_ignored():
    # seen live: keep=false on the judge's own #1 and #2 picks, reason ""
    vs = [{"index": i, "keep": i == 5, "hook": 3 - i % 2, "arc": 2 + (i < 2), "quote": 1, "standalone": 2,
           "trend": 2, "rank": i + 1, "reason": ""} for i in range(6)]
    out = _verify(vs, count=4, floor=40)
    assert len(out) == 4, len(out)


def test_a_rejection_with_a_reason_still_drops_the_clip():
    vs = [{"index": i, "keep": i != 0, "hook": 2, "arc": 2 + i % 2, "quote": 1, "standalone": 2, "trend": 2,
           "rank": i + 1, "reason": "garbled word salad" if i == 0 else ""} for i in range(6)]
    out = _verify(vs, count=6, floor=0)
    assert all(c.start_seconds != 0 for c in out)


def test_the_video_title_reaches_the_genre_prompt():
    seen = []
    real = cf._groq_chat
    cf._groq_chat = lambda client, model, prompt, *a, **k: seen.append(prompt) or SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"category":"reaction","confidence":0.9}'))])
    try:
        cat = cf.detect_category([_seg(0, 5, "hey guys welcome back")], _cfg(video_title="This Try Not To Laugh Was SO Hard"))
    finally:
        cf._groq_chat = real
    assert cat == "reaction" and "VIDEO TITLE: This Try Not To Laugh Was SO Hard" in seen[0]


def test_no_reaction_candidates_for_podcasts():
    cfg = _cfg(count=5, category="podcast")
    cf.apply_profile(cfg)
    assert cf._reaction_candidates(_challenge_video(), None, cfg) == []


def _rate_limited(retry_after: str):
    import groq
    import httpx
    resp = httpx.Response(429, headers={"retry-after": retry_after},
                          request=httpx.Request("POST", "https://api.groq.com/x"))
    return groq.RateLimitError("rate limited", response=resp, body=None)


def _flaky_client(errors: list, reply="ok"):
    """A Groq client whose create() raises each queued error once, then returns `reply`."""
    calls = []

    def create(**kw):
        calls.append(kw["model"])
        if errors:
            raise errors.pop(0)
        return reply
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def _no_sleep():
    slept, real = [], cf.time.sleep
    cf.time.sleep = slept.append
    return slept, real


def test_a_per_minute_rate_limit_is_waited_out():
    client, calls = _flaky_client([_rate_limited("7")])
    slept, real = _no_sleep()
    try:
        assert cf._groq_chat(client, "m", "p", 0.0) == "ok"
    finally:
        cf.time.sleep = real
    assert slept == [7] and calls == ["m", "m"]


def test_a_daily_limit_fails_fast_with_a_readable_message():
    client, calls = _flaky_client([_rate_limited("1800")])
    slept, real = _no_sleep()
    try:
        cf._groq_chat(client, "openai/gpt-oss-20b", "p", 0.0)
        raise AssertionError("expected GroqQuotaError")
    except cf.GroqQuotaError as e:
        assert "daily limit" in str(e) and "30 min" in str(e)
    finally:
        cf.time.sleep = real
    assert slept == [] and calls == ["openai/gpt-oss-20b"]       # no hour-long sleep


def test_judge_quota_spent_falls_back_to_the_picker_model():
    vs = [{"index": i, "keep": True, "hook": 3, "arc": 3, "quote": 2, "standalone": 2,
           "trend": 2, "rank": i + 1} for i in range(6)]
    reply, used = _scripted(vs)(), []

    def chat(client, model, prompt, temp, effort="low"):
        used.append(model)
        if model == "openai/gpt-oss-120b":
            raise cf.GroqQuotaError(model, 3600)
        return reply
    segs = [_seg(i * 30, i * 30 + 25, " ".join(["word"] * 40)) for i in range(6)]
    clips = [_clip(i * 30, i * 30 + 20, score=5) for i in range(6)]
    real, cf._groq_chat = cf._groq_chat, chat
    try:
        out = cf._verify_and_refine(clips, segs, _cfg(groq_verify_model="openai/gpt-oss-120b"))
    finally:
        cf._groq_chat = real
    assert used == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"] and len(out) == 3
    assert all(c._rank_pct is not None for c in out)              # verdicts were applied


def test_pick_windows_rotate_and_drop_a_spent_model():
    pick = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
        {"clips": [{"start_seconds": 10, "end_seconds": 30, "title": "t", "score": 7}]})),
        finish_reason="stop")])
    used, spent = [], {"openai/gpt-oss-120b": 1}     # 120b answers once, then its daily cap is gone

    def chat(client, model, prompt, temp, effort="low"):
        used.append(model)
        if model in spent:
            spent[model] -= 1
            if spent[model] < 0:
                raise cf.GroqQuotaError(model, 3600)
        return pick
    segs = [_seg(i * 20, i * 20 + 18, " ".join(["word"] * 30)) for i in range(50)]   # ~4 windows
    pool = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
    real, cf._groq_chat = cf._groq_chat, chat
    try:
        cf._find_clips_groq(segs, _cfg(groq_pick_models=pool), None)
    finally:
        cf._groq_chat = real
    assert used[:2] == pool                                        # windows alternate
    assert used[2:4] == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
    assert set(used[4:]) == {"openai/gpt-oss-20b"}                 # spent 120b dropped, never re-tried


def test_groq_budget_is_kept_per_model():
    from app import groq_gate
    seen = []
    fake = SimpleNamespace(incrby=lambda k, n: seen.append(k) or n, expire=lambda *a: None,
                           decrby=lambda *a: None)
    real, groq_gate._r = groq_gate._r, fake
    try:
        groq_gate.gate(100, "openai/gpt-oss-20b")
        groq_gate.gate(100, "openai/gpt-oss-120b")
    finally:
        groq_gate._r = real
    assert seen[0] != seen[1] and "gpt-oss-20b" in seen[0] and "gpt-oss-120b" in seen[1]


def test_slugs_are_ascii_so_they_can_name_a_bucket_object():
    import uuid
    from app import storage
    assert cf.slugify("Pokémon rage quit") == "pokemon-rage-quit"
    assert cf.slugify("Zoë loses it") == "zoe-loses-it"
    assert cf.slugify("日本語 moment") == "moment"
    for label in ("Pokémon rage quit", "日本語", "_whoosh", "--- !!! ---", "", "a" * 80 + " b"):
        slug = cf.slugify(label)
        storage.clip_key(uuid.uuid4(), uuid.uuid4(), f"01_{slug}_cc.mp4")   # raises if unsafe
        storage.user_sfx_key(uuid.uuid4(), f"{slug}.mp3")                    # sfx: slug comes first


def test_a_non_numeric_judge_score_does_not_fail_the_analysis():
    verdicts = [{"index": i, "keep": True, "hook": "high" if i == 0 else 2, "arc": "2/3" if i == 1 else 2,
                 "quote": None, "standalone": float("nan") if i == 2 else 1, "trend": 1,
                 "rank": i + 1, "reason": 5} for i in range(6)]
    out = _verify(verdicts)
    assert out                                          # it used to raise ValueError: 'high'


def test_a_cancelled_job_stops_its_running_processes():
    import threading
    import time
    seen = {}

    def work():
        with cf.proc_scope("test-cancel"):
            try:
                cf.run([sys.executable, "-c", "import time; time.sleep(60)"])
            except cf.Cancelled:
                seen["cancelled"] = True
            try:                                        # and nothing new starts after it
                cf.run([sys.executable, "-c", "pass"])
            except cf.Cancelled:
                seen["refused"] = True

    t0 = time.monotonic()
    t = threading.Thread(target=work)
    t.start()
    for _ in range(200):                                # wait for the process to register
        if cf._PROCS.get("test-cancel"):
            break
        time.sleep(0.05)
    cf.cancel_scope("test-cancel")
    t.join(20)
    assert not t.is_alive() and time.monotonic() - t0 < 20
    assert seen == {"cancelled": True, "refused": True}, seen
    assert "test-cancel" not in cf._CANCELLED and "test-cancel" not in cf._ACTIVE_SCOPES
    cf.cancel_scope("test-cancel")                      # after the scope ended: a no-op
    assert "test-cancel" not in cf._CANCELLED


def test_has_audio_tells_a_picture_only_file_from_one_with_sound():
    if not shutil.which(cf.FFMPEG) and not Path(cf.FFMPEG).exists():
        print("    (skipped: ffmpeg not installed)")
        return
    picture = ["-f", "lavfi", "-i", "testsrc=size=160x90:rate=5:duration=1"]
    sound = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    with tempfile.TemporaryDirectory() as d:
        silent, voiced = Path(d) / "silent.mp4", Path(d) / "voiced.mp4"
        subprocess.run([cf.FFMPEG, "-v", "error", "-y", *picture, str(silent)], check=True)
        subprocess.run([cf.FFMPEG, "-v", "error", "-y", *picture, *sound, "-shortest", str(voiced)], check=True)
        assert cf.has_audio(silent) is False
        assert cf.has_audio(voiced) is True


SIGNED = "https://acct.r2.cloudflarestorage.com/kappacut/users/u/source.mp4?X-Amz-Signature=SECRETSIG&X-Amz-Credential=SECRETKEY"


def _failure_of(*cmd: str) -> str:
    try:
        cf.run(list(cmd))
    except RuntimeError as e:
        return str(e)
    raise AssertionError("the command was meant to fail")


def test_a_failed_command_never_shows_a_signed_url():
    # how ffmpeg reports a source it can't open
    msg = _failure_of(sys.executable, "-c",
                      f"import sys; sys.stderr.write(\"{SIGNED}: Server returned 403 Forbidden\\n\"); sys.exit(1)")
    assert "SECRET" not in msg and "X-Amz" not in msg
    assert "<url>" in msg and "Server returned 403" in msg           # the useful part stays


def test_a_signed_url_cut_by_the_stderr_limit_stays_hidden():
    # the URL straddles the 1000-character tail, so only its query string survives the cut
    tail = " " + "x" * (999 - len(SIGNED) // 2)
    msg = _failure_of(sys.executable, "-c",
                      f"import sys; sys.stderr.write({SIGNED + tail!r}); sys.exit(1)")
    assert "SECRET" not in msg and "X-Amz" not in msg


def test_a_url_in_the_command_itself_is_hidden_too():
    # the web process's waveform command has `-i <url>` inside the first four arguments
    msg = _failure_of(sys.executable, "-c", "import sys; sys.exit(1)", SIGNED)
    assert "SECRET" not in msg


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
