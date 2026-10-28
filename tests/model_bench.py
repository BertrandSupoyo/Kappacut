"""Model benchmark on the RUNNING dev stack, with a hard spend cap.

The test behind README "The clip-picking model" (2026-10-25). Per stored video (one per
distinct source): the configured picker pool once, then the judge 3x at low effort
(5,000-token reply cap, as in production) and 3x at medium (12,000-token cap, so it can
finish). Every call is priced from Groq's reported usage; before each call the worst case
(prompt + a full-length reply) is checked against the budget and the run stops rather than
cross it. Reuses stored transcripts (no Whisper). Writes nothing to the DB or the bucket.

Not collected by pytest/CI (no `test_` prefix): it spends real Groq credit. Run it inside
the worker container; the budget defaults to $0.50:

    docker exec -i -w /srv -e MODEL_BENCH_BUDGET=0.50 kappacut-worker-1 \
        python - < tests/model_bench.py

Prints tokens, spend, per-run status and agreement; full per-call detail goes to
/tmp/model_bench.json in the container.
"""
import asyncio
import contextlib
import copy
import io
import itertools
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import asyncpg
from sqlalchemy.engine import make_url

import kappacut as cf
import pipeline
from app import groq_gate, storage
from app.config import settings
from app.jobs import _apply_options

BUDGET = float(os.environ.get("MODEL_BENCH_BUDGET", "0.50"))
PRICE = {"openai/gpt-oss-20b": (0.075, 0.30), "openai/gpt-oss-120b": (0.15, 0.60)}   # $/1M in, out (Groq list)
LOW_CAP, MEDIUM_CAP = cf.GROQ_PICK_MAX_TOKENS, 12000
RUNS = (("low", LOW_CAP), ("low", LOW_CAP), ("low", LOW_CAP),
        ("medium", MEDIUM_CAP), ("medium", MEDIUM_CAP), ("medium", MEDIUM_CAP))


class BudgetStop(BaseException):
    """BaseException so the engine's broad `except Exception` can't swallow it."""


settings.groq_tokens_per_minute = 200_000     # this process only: paid tier allows 250k
groq_gate.install()

calls: list[dict] = []
spent = [0.0]
STAGE = ["setup"]
_real_chat = cf._groq_chat


def metered_chat(client, model, prompt, temperature, effort="low"):
    pin, pout = PRICE[model]
    worst = (len(prompt) / 3 * pin + cf.GROQ_PICK_MAX_TOKENS * pout) / 1e6
    if spent[0] + worst > BUDGET:
        raise BudgetStop(f"next call could cost ${worst:.4f}; ${spent[0]:.4f} of ${BUDGET} spent")
    t = time.monotonic()
    resp = _real_chat(client, model, prompt, temperature, effort)
    u = resp.usage
    det = getattr(u, "completion_tokens_details", None)
    pdet = getattr(u, "prompt_tokens_details", None)
    cost = (u.prompt_tokens * pin + u.completion_tokens * pout) / 1e6   # full price: cached input billed as uncached
    spent[0] += cost
    ct = getattr(u, "completion_time", None) or 0
    calls.append({"stage": STAGE[0], "model": model.split("/")[-1], "effort": effort,
                  "cap": cf.GROQ_PICK_MAX_TOKENS, "in": u.prompt_tokens, "out": u.completion_tokens,
                  "cached": getattr(pdet, "cached_tokens", None) if pdet else None,
                  "reasoning": getattr(det, "reasoning_tokens", None) if det else None,
                  "secs": round(time.monotonic() - t, 1),
                  "tok_per_s": round(u.completion_tokens / ct) if ct else None,
                  "finish": resp.choices[0].finish_reason, "usd": round(cost, 5)})
    return resp


cf._groq_chat = metered_chat


async def load():
    u = make_url(os.environ["DATABASE_URL"])
    con = await asyncpg.connect(host=u.host, port=u.port, user=u.username,
                                password=u.password, database=u.database)
    rows = await con.fetch(
        "select distinct on (p.source_bytes, p.duration_seconds) "
        "p.id, p.user_id, p.name, p.source_filename, p.duration_seconds, "
        "a.segments, a.category, "
        "(select j.payload from jobs j where j.project_id=p.id and j.kind='analyze' "
        " order by j.created_at desc limit 1) payload "
        "from projects p join analyses a on a.project_id=p.id "
        "order by p.source_bytes, p.duration_seconds, a.created_at desc")
    await con.close()
    return sorted(rows, key=lambda r: -r["duration_seconds"])


def clip_row(c, segs, loud, ew):
    return {"start": round(c.start_seconds, 1), "end": round(c.end_seconds, 1), "title": c.title,
            "virality": cf.score_breakdown(c, segs, loud, ew)["total"]}


def same(a, b):
    ov = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return ov >= 0.5 * min(a["end"] - a["start"], b["end"] - b["start"])


def overlap(xs, ys):
    if not xs or not ys:
        return None
    return sum(1 for x in xs if any(same(x, y) for y in ys)) / max(len(xs), len(ys))


def status_of(log: str, stage_calls: list[dict]) -> str:
    if "no usable verdicts" in log or "flow check skipped" in log:
        return "cut off" if any(c["finish"] == "length" for c in stage_calls) else "no verdicts"
    if "still looks broken" in log:
        return "broken"
    if "looks broken" in log:
        return "ok after retry"
    return "ok"


def run_video(row, out):
    segs = json.loads(row["segments"])
    payload = json.loads(row["payload"]) if row["payload"] else {}
    cfg = _apply_options(pipeline.get_cfg(), payload)
    cfg["video_title"] = row["name"] or row["source_filename"] or ""
    cfg["category"] = cfg.get("category_override") or row["category"] or "general"
    cf.apply_profile(cfg)
    ew = float(cfg.get("energy_weight", 1.0))
    vid = f"{row['duration_seconds'] / 60:.1f} min"
    v = {"video": vid, "category": cfg["category"], "runs": []}
    out["videos"].append(v)
    print(f"\n### {vid} ({cfg['category']}) ###", flush=True)

    with tempfile.TemporaryDirectory(prefix="model_test2_") as tmp:
        key = storage.find_source(row["user_id"], row["id"])
        src = storage.download(key, Path(tmp) / ("src" + Path(key).suffix))
        loud = cf.loudness_track(cf.extract_audio(src, Path(tmp)))

    STAGE[0] = f"{vid} pick"
    t = time.monotonic()
    pool, _ = cf.find_clips(segs, cfg, loud)
    v["pick_s"] = round(time.monotonic() - t, 1)
    v["candidates"] = len(pool)
    print(f"picked {len(pool)} candidates in {v['pick_s']}s  (spent ${spent[0]:.4f})", flush=True)

    for i, (effort, cap) in enumerate(RUNS, 1):
        STAGE[0] = f"{vid} judge-{effort}-{i}"
        cf.GROQ_PICK_MAX_TOKENS = cap
        buf = io.StringIO()
        t = time.monotonic()
        try:
            with contextlib.redirect_stdout(buf):
                res = cf._verify_and_refine(copy.deepcopy(pool), segs,
                                            dict(cfg, groq_verify_effort=effort), loud)
        finally:
            cf.GROQ_PICK_MAX_TOKENS = LOW_CAP
            sys.stdout.write(buf.getvalue())
        sc = [c for c in calls if c["stage"] == STAGE[0]]
        r = {"effort": effort, "cap": cap, "status": status_of(buf.getvalue(), sc),
             "secs": round(time.monotonic() - t, 1), "calls": len(sc),
             "out_tokens": sum(c["out"] for c in sc), "reasoning": sum(c["reasoning"] or 0 for c in sc),
             "usd": round(sum(c["usd"] for c in sc), 5),
             "clips": [clip_row(c, segs, loud, ew) for c in res]}
        v["runs"].append(r)
        print(f"judge {effort:6s} #{i}: {r['status']:15s} {len(res)} clips  {r['secs']:6.1f}s  "
              f"out {r['out_tokens']:6d}  ${r['usd']:.4f}  (total ${spent[0]:.4f})", flush=True)

    good = {e: [r["clips"] for r in v["runs"] if r["effort"] == e and r["status"] in ("ok", "ok after retry")]
            for e in ("low", "medium")}
    v["agreement"] = {
        "low_vs_low": [overlap(a, b) for a, b in itertools.combinations(good["low"], 2)],
        "medium_vs_medium": [overlap(a, b) for a, b in itertools.combinations(good["medium"], 2)],
        "low_vs_medium": [overlap(a, b) for a in good["low"] for b in good["medium"]],
    }


def main():
    out = {"budget_usd": BUDGET, "videos": [], "stopped": None}
    try:
        for row in asyncio.run(load()):
            run_video(row, out)
    except BudgetStop as e:
        out["stopped"] = str(e)
        print(f"\nSTOPPED AT BUDGET: {e}", flush=True)
    out["calls"] = calls
    out["spent_usd"] = round(spent[0], 5)
    out["tokens"] = {m: {"in": sum(c["in"] for c in calls if c["model"] == m),
                         "out": sum(c["out"] for c in calls if c["model"] == m),
                         "calls": sum(1 for c in calls if c["model"] == m)}
                     for m in sorted({c["model"] for c in calls})}
    Path("/tmp/model_bench.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    print("\n=== tokens used ===")
    for m, t in out["tokens"].items():
        print(f"{m:14s} {t['calls']:3d} calls  in {t['in']:7d}  out {t['out']:7d}")
    print(f"spent ${out['spent_usd']:.4f} of ${BUDGET:.2f} cap")
    print("\n=== agreement between good runs (share of clips that match) ===")
    for v in out["videos"]:
        for k, xs in v.get("agreement", {}).items():
            vals = [round(x, 2) for x in xs if x is not None]
            print(f"{v['video']:9s} {k:17s} {vals}")


main()
