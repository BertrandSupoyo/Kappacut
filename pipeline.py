"""Web-facing wrapper around clipfinder.py.

`analyze()`  — transcribe + rank highlights, NO cutting.
`render()`   — cut a list of ranges (AI-accepted or hand-drawn) with clipfinder's cut_clip.

Everything heavy is delegated to clipfinder.py; this file is orchestration only.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

import clipfinder as cf

HERE = Path(__file__).resolve().parent


def get_cfg() -> dict:
    """Engine tuning for the web app. clipfinder.toml holds the knobs; API keys and any
    overrides come from the process environment (the container / .env). The old
    web_data/_settings.json overlay went away with the Settings page in Phase 3."""
    cf.load_dotenv(HERE / ".env")          # no-op in the container; loads keys in local dev
    cfg = cf.load_config(HERE / "clipfinder.toml")
    cfg["dry_run"] = False
    cfg["out"] = ""
    cfg["word_timestamps"] = True          # web app always wants per-word times (karaoke + snapping)
    return cfg


ProgressCB = Callable[[int, str], None]


def analyze(video_path: Path, cfg: dict, on_progress: ProgressCB = lambda p, m: None) -> dict:
    """Transcribe the video and rank standalone-clip candidates. No files are cut.

    Returns {duration, segments, clips} where each clip is Clip.model_dump() plus
    `duration` and (when vision is on) `visual_score` / `visual_note`.
    """
    video_path = Path(video_path)
    cf.budget_reset(cfg)
    on_progress(5, "Reading the video")
    duration = cf.probe_duration(video_path)

    with tempfile.TemporaryDirectory(prefix="clipfinder_web_") as tmp:
        workdir = Path(tmp)

        on_progress(12, "Pulling the audio")
        mp3 = cf.extract_audio(video_path, workdir)

        on_progress(28, "Reading the energy")
        loud = cf.loudness_track(mp3)

        on_progress(35, "Transcribing every line")
        segments = cf.transcribe(mp3, workdir, cfg)
        if not segments:
            return {"duration": duration, "segments": [], "clips": []}

        on_progress(55, "Reading the room")
        cfg["category"] = cf.detect_category(segments, cfg)

        on_progress(65, "Ranking the moments")
        clips, _usage = cf.find_clips(segments, cfg, loud)

        if cfg.get("coherence_check", True) and clips:
            on_progress(85, "Checking the flow of each moment")
            clips = cf._verify_and_refine(clips, segments, cfg)

        cf.attach_auto(clips, loud, cfg.get("category") or "")   # sound + effects, on the final bounds

        visuals: dict[int, cf.VisualNote] = {}
        vmodel = cf._vision_model(cfg)
        if cfg.get("vision") and clips and cf._affordable(
                cfg, vmodel, len(clips) * 1700 + 300, len(clips) * 140, "vision"):
            on_progress(92, "Looking at a frame from each clip")
            try:
                visuals = cf.vision_check(video_path, clips, workdir, vmodel, cfg)
            except Exception:
                visuals = {}

        records = []
        for i, c in enumerate(clips):
            rec = c.model_dump()
            rec["duration"] = round(c.end_seconds - c.start_seconds, 1)
            if getattr(c, "_auto", None):
                rec["auto"] = c._auto                    # suggested reframe / caption / colour / sfx
            if i in visuals:
                rec["visual_score"] = visuals[i].visual_score
                rec["visual_note"] = visuals[i].note
            records.append(rec)

    on_progress(100, "done")
    out = {"duration": duration, "segments": segments, "clips": records,
           "category": cfg.get("category")}
    sp = cfg.get("_spend") or {}
    if sp.get("cap") or sp.get("events"):
        out["spend"] = {
            "usd": round(sp.get("usd", 0.0), 4),
            "cap": sp.get("cap", 0.0),
            "fellback": sp.get("fellback", False),
            "models": sorted({e["model"] for e in sp.get("events", [])}),
        }
    return out


# ---------------------------------------------------------------- captions

CAP_STYLES: dict[str, dict] = {
    #                fontname       size  bold  primary        outline_col     box?  outline shadow  highlight
    "clean": dict(font="Arial",       size=52, bold=-1, primary="&H00FFFFFF", oc="&H00000000", box=1, outline=2.4, shadow=0, hl="&H0033F0FF"),
    "bold":  dict(font="Arial",       size=62, bold=-1, primary="&H00FFFFFF", oc="&H00000000", box=1, outline=4.0, shadow=1, hl="&H0033F0FF"),
    "box":   dict(font="Arial",       size=50, bold=-1, primary="&H00FFFFFF", oc="&H00000000", box=3, outline=8.0, shadow=0, hl="&H0033F0FF"),
    "pop":   dict(font="Arial Black", size=68, bold=-1, primary="&H0033F0FF", oc="&H00101010", box=1, outline=5.0, shadow=1, hl="&H00FFFFFF"),
}
_ALIGN = {"bottom": 2, "center": 5, "top": 8}
_KAR_MAX_WORDS, _KAR_MAX_SPAN = 5, 2.6


def _ass_ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _local_words(sg: dict, start: float, end: float) -> list[dict]:
    out = []
    for w in sg.get("words") or []:
        if w["end"] > start and w["start"] < end and (w.get("w") or "").strip():
            out.append({"w": w["w"].strip(),
                        "start": round(max(w["start"], start) - start, 2),
                        "end": round(min(w["end"], end) - start, 2)})
    return out


def caption_lines(segments: list[dict], start: float, end: float) -> list[dict]:
    """Transcript lines overlapping [start, end], re-based to clip-local time (with words)."""
    out = []
    for sg in segments or []:
        a, b = float(sg["start"]), float(sg["end"])
        if b <= start or a >= end:
            continue
        out.append({"start": round(max(a, start) - start, 2),
                    "end": round(min(b, end) - start, 2),
                    "text": (sg.get("text") or "").strip(),
                    "words": _local_words(sg, start, end)})
    return out


def _clean_txt(t: str, upper: bool) -> str:
    t = (t or "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()
    return t.upper() if upper else t


def _chunk_words(ws: list[dict]) -> list[list[dict]]:
    ws = sorted(ws, key=lambda w: w["start"])
    groups, cur = [], []
    for w in ws:
        cur.append(w)
        full = len(cur) >= _KAR_MAX_WORDS or w["end"] - cur[0]["start"] > _KAR_MAX_SPAN
        if full or (w["w"][-1:] in ".!?" and len(cur) >= 3):
            groups.append(cur); cur = []
    if cur:
        groups.append(cur)
    merged: list[list[dict]] = []                            # fold tiny trailing groups back
    for g in groups:
        if merged and len(g) <= 1 and len(merged[-1]) <= _KAR_MAX_WORDS:
            merged[-1].extend(g)
        else:
            merged.append(g)
    return merged


_ANIMS = {"none", "pop", "fade", "slide", "bounce"}


def _anim_lead(anim: str, pos_set: bool, px: int, py: int, karaoke: bool) -> str:
    """The leading override block for one caption line: placement + entrance animation."""
    anim = anim if anim in _ANIMS else "none"
    if karaoke:                                   # karaoke IS the animation; only a soft fade on top
        base = f"\\an5\\pos({px},{py})" if pos_set else ""
        return "{" + base + ("\\fad(60,0)" if anim != "none" else "") + "}" if (base or anim != "none") else ""
    tags = ""
    if pos_set and anim == "slide":
        tags = f"\\an5\\move({px},{py + 46},{px},{py},0,170)\\fad(110,0)"
    elif pos_set:
        tags = f"\\an5\\pos({px},{py})"
    if anim == "pop":
        tags += "\\fad(45,0)\\fscx55\\fscy55\\t(0,130,\\fscx100\\fscy100)"
    elif anim == "fade":
        tags += "\\fad(150,90)"
    elif anim == "bounce":
        tags += "\\fad(40,0)\\fscx70\\fscy70\\t(0,95,\\fscx110\\fscy110)\\t(95,175,\\fscx100\\fscy100)"
    elif anim == "slide" and not pos_set:
        tags += "\\fad(140,0)"
    return "{" + tags + "}" if tags else ""


def build_ass(lines: list[dict], style: str, position: str, size: float,
              vertical: bool, path: Path, karaoke: bool = False,
              pos: dict | None = None, anim: str = "none") -> Path:
    st = CAP_STYLES.get(style, CAP_STYLES["clean"])
    play_x, play_y = (1080, 1920) if vertical else (1920, 1080)
    fs = round(st["size"] * float(size or 1.0))
    # pos = {"x","y"} 0..1 -> anchor the line's centre there via \an5\pos(); else fall back to the preset
    px = py = 0
    pos_set = bool(pos and pos.get("x") is not None and pos.get("y") is not None)
    if pos_set:
        px = round(max(0.0, min(1.0, float(pos["x"]))) * play_x)
        py = round(max(0.0, min(1.0, float(pos["y"]))) * play_y)
        align, margin_v = 5, 0
    else:
        align = _ALIGN.get(position, 2)
        margin_v = 120 if align != 5 else 0
    lead_tag = _anim_lead(anim, pos_set, px, py, karaoke)
    back = "&H90000000" if st["box"] == 3 else "&H00000000"
    upper = style == "pop"

    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: cap,%s,%d,%s,&H00FFFFFF,%s,%s,%d,0,0,0,100,100,0,0,%d,%.1f,%d,%d,80,80,%d,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ) % (play_x, play_y, st["font"], fs, st["primary"], st["oc"], back, st["bold"],
         st["box"], st["outline"], st["shadow"], align, margin_v)

    def dlg(a, b, text):
        return f"Dialogue: 0,{_ass_ts(a)},{_ass_ts(max(b, a + 0.05))},cap,,0,0,0,,{lead_tag}{text}"

    rows = []
    for ln in lines:
        ws = ln.get("words") if karaoke else None
        if ws:
            for g in _chunk_words(ws):
                g_end = g[-1]["end"]
                for i, w in enumerate(g):
                    a = w["start"]
                    b = g[i + 1]["start"] if i + 1 < len(g) else g_end
                    parts = []
                    for j, ww in enumerate(g):
                        t = _clean_txt(ww["w"], upper)
                        parts.append(f"{{\\c{st['hl']}\\b1\\fscx114\\fscy114}}{t}{{\\r}}" if j == i else t)
                    rows.append(dlg(a, b, " ".join(parts)))
        else:
            txt = _clean_txt(ln.get("text"), upper)
            if txt:
                rows.append(dlg(ln["start"], ln["end"], txt))

    path.write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- render

def render(video_path: Path, ranges: list[dict], cfg: dict, out_dir: Path,
           segments: list[dict] | None = None, sfx_dir: Path | None = None,
           on_clip: Callable[[dict], None] | None = None,
           on_stage: Callable[[str], None] | None = None) -> list[dict]:
    """Cut each range to its own mp4 in out_dir. `ranges` items:
    {"start", "end", "vertical", "label", "crop": {"mode","px","py","zoom","blur"}|null,
     "captions": {"style","position","size","anim","lines":[{start,end,text}]}|null,
     "sfx": [{"file","at","gain"}]|null,
     "color": {"preset","b","c","s"}|null}
    Caption times inside `lines` and `sfx[].at` are clip-local (0 = clip start).
    `sfx` is mixed in a second pass (video copied) when `sfx_dir` is given.
    `crop` (only when vertical): mode "crop" -> px/py 0..1 pan + zoom 1..4 punch-in;
    mode "fill" -> full video centred, blurred copy (sigma `blur`) fills the top/bottom bars.
    """
    video_path, out_dir = Path(video_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = cf.probe_duration(video_path)

    out = []
    total = len(ranges)
    for i, r in enumerate(ranges, 1):
        start = max(0.0, float(r["start"]))
        end = min(dur, float(r["end"]))
        if end - start < 1.0:
            continue
        if on_stage:
            on_stage(f"Rendering clip {i} of {total}")
        vertical = bool(r.get("vertical"))
        crop = r.get("crop") if vertical else None
        _cm = crop.get("mode") if isinstance(crop, dict) else None
        vtag = ("_fill" if _cm == "fill" else "_subject" if _cm == "cropfill"
                else "_vertical" if vertical else "")
        stem = f"{i:02d}_{cf.slugify(r.get('label') or 'clip')}"
        name = f"{stem}{vtag}{'_cc' if r.get('captions') else ''}.mp4"

        subs = None
        cap = r.get("captions")
        if cap:
            lines = cap.get("lines") or caption_lines(segments, start, end)
            if lines:
                subs = build_ass(lines, cap.get("style", "clean"),
                                 cap.get("position", "bottom"), cap.get("size", 1.0),
                                 vertical, out_dir / f"{stem}.ass",
                                 karaoke=bool(cap.get("karaoke")), pos=cap.get("pos"),
                                 anim=cap.get("anim", "none"))

        cf.cut_clip(video_path, start, end, out_dir / name, vertical, subs=subs, crop=crop,
                    loudnorm=cfg.get("loudnorm", True), color=r.get("color"))

        sfx = [s for s in (r.get("sfx") or []) if isinstance(s, dict) and s.get("file")]
        if sfx and sfx_dir is not None:
            mixed = out_dir / f"_sfxmix{name}"
            try:
                if cf.mix_sfx(out_dir / name, sfx, Path(sfx_dir), mixed):
                    mixed.replace(out_dir / name)
            finally:
                mixed.unlink(missing_ok=True)

        rec = {"file": name, "start": round(start, 2), "end": round(end, 2),
               "duration": round(end - start, 1), "vertical": vertical,
               "captions": bool(subs), "sfx": len(sfx)}
        out.append(rec)
        if on_clip:
            on_clip(rec)
    return out
