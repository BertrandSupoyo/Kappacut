#!/usr/bin/env python3
"""
clipfinder.py - find the best short-form clips in long videos. No training, no local ML.

Pipeline per video:
    ffmpeg  ->  compressed mono audio
    Groq    ->  timestamped transcript          (whisper-large-v3-turbo, free)
    LLM     ->  ranked list of standalone clips  (Groq gpt-oss = free, or Claude = better)
    snap    ->  clip edges pulled to sentence / pause boundaries (no mid-word cuts)
    ffmpeg  ->  cut each clip  (+ optional 9:16 vertical render)

Config: edit clipfinder.toml next to this script. CLI flags override it.

Usage
-----
    python clipfinder.py "C:/path/to/video.mp4"
    python clipfinder.py "C:/Users/leosa/Downloads/Video/streamer"          # whole folder
    python clipfinder.py video.mp4 --n 8 --taste "chaotic reactions; skip slow talking"
    python clipfinder.py video.mp4 --picker claude          # better picks, ~$0.05/video
    python clipfinder.py video.mp4 --vertical               # also render 1080x1920 crop
    python clipfinder.py video.mp4 --lead 0.5 --tail 1.0    # more breathing room per clip
    python clipfinder.py video.mp4 --dry-run                # transcript + picks, no cutting

Keys - clipfinder .env (or real env vars):
    GROQ_API_KEY=gsk_...          # console.groq.com   (free - always needed)
    ANTHROPIC_API_KEY=sk-ant-...  # console.anthropic.com  (only for --picker claude / --vision)
"""
from __future__ import annotations

import argparse
import base64
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator

HERE = Path(__file__).resolve().parent

# ------------------------------------------------------------------ config

DEFAULTS: dict = {
    # picker
    "backend": "groq",                       # "groq" (free) | "claude"
    "groq_model": "openai/gpt-oss-20b",       # 20b: fast, light on the free tier. 120b: better picks, slower
    "claude_model": "claude-opus-5",          # picks the moments (stage 1) — or claude-sonnet-5 / claude-haiku-4-5
    "verify_model": "",                        # stage-2 flow check; "" = same as claude_model
    "groq_verify_model": "",                   # stage-2 judge on the Groq backend; "" = same as groq_model
    "quality_floor": 40,                       # 0-100: clips below it are dropped rather than padded in
    "vision_model": "",                        # one-frame vision pass; "" = same as claude_model
    "claude_budget": 0.0,                      # USD cap on Claude spend per video; 0 = no cap. Over cap -> fall back to Groq
    "temperature": 0.3,
    # clips
    "count": 6,
    "taste": "",
    "min_seconds": 10.0,
    "max_seconds": 45.0,
    "lead_seconds": 0.4,                      # padding kept before the snapped start
    "tail_seconds": 0.8,                      # padding kept after the snapped end
    "snap_max_shift": 4.0,                    # how far an edge may move to reach a clean boundary
    "min_words": 5,                           # drop clips with fewer spoken words (unless score >= 9.5)
    "max_gap_seconds": 6.0,                   # a silent stretch this long inside a clip = a moment boundary
    # transcript cleanup (rules, no ML)
    "filter_hallucinations": True,
    "min_avg_logprob": -1.15,                 # Whisper confidence floor; below this = drop the segment
    "max_compression_ratio": 2.5,             # above this = repetitive hallucination
    "coherence_check": True,                  # one extra LLM call: drop clips whose transcript is word-salad
    "word_timestamps": False,                 # ask Whisper for per-word times (for karaoke captions)
    # output
    "vertical": False,
    "vision": False,
    "loudnorm": True,                         # normalize each rendered clip to -14 LUFS (social target)
}

GROQ_WHISPER = "whisper-large-v3-turbo"       # or "whisper-large-v3" (slower, ~same)
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".ts", ".avi", ".m4v"}
GROQ_MAX_BYTES = 24 * 1024 * 1024            # free tier caps audio upload at 25 MB
AUDIO_CHUNK_SEC = 1200                        # 20-min audio chunks when a file is too big
PICKER_WINDOW_SEC = 240                       # transcript window per Groq call (free tier: 8k TPM)
GROQ_PICK_MAX_TOKENS = 5000

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

COST_PER_MTOK = {
    "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0),
}


# ---- mixed Claude models + a per-video spend cap ---------------------------
# Each LLM stage can run on its own Claude model (pick / verify / vision). One
# ledger per video tracks Claude spend; when a call would push past claude_budget
# that stage falls back to free Groq instead. Everything degrades to Groq.

def _cost(model: str, in_tok: int, out_tok: int) -> float:
    ci, co = COST_PER_MTOK.get(model, (0.0, 0.0))
    return in_tok / 1e6 * ci + out_tok / 1e6 * co


def _rough_tokens(s: str) -> int:
    return len(s) // 4 + 1


def budget_reset(cfg: dict) -> None:
    """Start a fresh Claude spend ledger on cfg (call once per video)."""
    cfg["_spend"] = {"usd": 0.0, "cap": max(0.0, float(cfg.get("claude_budget") or 0.0)),
                     "events": [], "fellback": False}


def _ledger(cfg: dict) -> dict:
    if cfg.get("_spend") is None:
        budget_reset(cfg)
    return cfg["_spend"]


def _budget_left(cfg: dict) -> float | None:
    """USD still available for Claude this video, or None when there is no cap."""
    sp = _ledger(cfg)
    return None if sp["cap"] <= 0 else max(0.0, sp["cap"] - sp["usd"])


def _budget_charge(cfg: dict, model: str, usage, stage: str) -> None:
    if usage is None:
        return
    sp = _ledger(cfg)
    c = _cost(model, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0))
    sp["usd"] += c
    sp["events"].append({"stage": stage, "model": model, "usd": round(c, 4)})


def _affordable(cfg: dict, model: str, in_tok: int, out_tok: int, stage: str) -> bool:
    """True if an estimated Claude call still fits the budget (or there is no cap)."""
    left = _budget_left(cfg)
    if left is None:
        return True
    est = _cost(model, in_tok, out_tok)
    if est <= left + 1e-9:
        return True
    _ledger(cfg)["fellback"] = True
    print(f"        {stage}: ~${est:.3f} would exceed ${left:.3f} left in the Claude budget", flush=True)
    return False


def _verify_model(cfg: dict) -> str:
    return cfg.get("verify_model") or cfg["claude_model"]


def _vision_model(cfg: dict) -> str:
    return cfg.get("vision_model") or cfg["claude_model"]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: Path) -> dict:
    cfg = dict(DEFAULTS)
    if path.exists():
        try:
            import tomllib
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"warning: could not read {path.name}: {e}")
            return cfg
        for section in raw.values():
            if isinstance(section, dict):
                for k, v in section.items():
                    if k in cfg:
                        cfg[k] = v
    return cfg


# ------------------------------------------------------------------ ffmpeg helpers

# Optional cap set by the multi-user worker (app/worker.py) / web process (app/main.py) so
# concurrent jobs share the box predictably instead of each ffmpeg grabbing every core.
# 0 = ffmpeg's own auto-detection (today's single-user CLI behaviour, unchanged).
FFMPEG_THREADS = 0


def _thread_args() -> list[str]:
    """`-threads` is a per-stream option: placed BEFORE `-i` it caps the decoder, placed
    before the output file it caps the encoder. Both need it — libx264 is the expensive
    half, and for a long time only the decoder was actually being capped here."""
    return ["-threads", str(FFMPEG_THREADS)] if FFMPEG_THREADS > 0 else []


def _filter_thread_args(complex_graph: bool = False) -> list[str]:
    """Neither `-threads` caps the FILTER graph, and the blur-fill path (gblur at sigma up
    to 80) is the most expensive filter this app runs — so cap that separately."""
    if FFMPEG_THREADS <= 0:
        return []
    flag = "-filter_complex_threads" if complex_graph else "-filter_threads"
    return [flag, str(FFMPEG_THREADS)]


def run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if p.returncode:
        raise RuntimeError(f"command failed: {' '.join(str(c) for c in cmd[:4])} ...\n{p.stderr[-1000:]}")
    return p


def probe_duration(path: Path) -> float:
    p = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", str(path)])
    return float(p.stdout.strip() or 0.0)


def extract_audio(video: Path, workdir: Path) -> Path:
    out = workdir / "audio.mp3"
    run([FFMPEG, "-y", *_thread_args(), "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "libmp3lame", "-b:a", "48k", str(out)])
    return out


def split_audio(mp3: Path, workdir: Path) -> list[tuple[Path, float]]:
    if mp3.stat().st_size <= GROQ_MAX_BYTES:
        return [(mp3, 0.0)]
    dur = probe_duration(mp3)
    parts, t, i = [], 0.0, 0
    while t < dur:
        part = workdir / f"chunk_{i:03d}.mp3"
        run([FFMPEG, "-y", "-ss", f"{t:.2f}", "-i", str(mp3),
             "-t", str(AUDIO_CHUNK_SEC), "-c", "copy", str(part)])
        parts.append((part, t))
        t += AUDIO_CHUNK_SEC
        i += 1
    return parts


def _aspect_ratio(s: str) -> float:
    """'16/9' -> 1.777 ; the width:height ratio of a cropfill foreground rectangle."""
    try:
        a, b = str(s).replace(":", "/").split("/")
        r = float(a) / float(b)
        return r if 0.2 < r < 5.0 else 16 / 9
    except Exception:
        return 16 / 9


# ---- colour grade: ONE definition for preview and render ---------------------
# Presets live in frontend/src/editor/colorPresets.json (copied next to this file as
# color_presets.json in the image). Both sides turn preset + sliders into the same 3x4
# affine matrix on gamma-encoded RGB: the editor applies it as an SVG feColorMatrix, the
# render as an ffmpeg lut3d. The old version defined the presets twice - CSS filters vs
# colortemperature/curves/eq - and every preset rendered differently from its preview.

_LUMA = (0.2126, 0.7152, 0.0722)          # the weights SVG/CSS saturate() uses
_IDENTITY = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))


@functools.lru_cache(maxsize=1)
def _color_presets() -> dict[str, list]:
    for p in (HERE / "color_presets.json", HERE / "frontend" / "src" / "editor" / "colorPresets.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, list)}
        except Exception:  # noqa: BLE001
            continue
    return {"none": []}


def _op_matrix(op: str, v: float) -> tuple:
    if op == "temp":
        return ((1 + v, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1 - v, 0))
    if op == "saturate":
        r, g, b = _LUMA
        return ((r + (1 - r) * v, g - g * v, b - b * v, 0),
                (r - r * v, g + (1 - g) * v, b - b * v, 0),
                (r - r * v, g - g * v, b + (1 - b) * v, 0))
    if op == "contrast":
        o = 0.5 * (1 - v)
        return ((v, 0, 0, o), (0, v, 0, o), (0, 0, v, o))
    if op == "brightness":
        return ((v, 0, 0, 0), (0, v, 0, 0), (0, 0, v, 0))
    return _IDENTITY


def _then(a: tuple, b: tuple) -> tuple:
    """The affine map 'apply a, then b'."""
    return tuple(
        tuple(sum(b[i][k] * a[k][j] for k in range(3)) for j in range(3))
        + (sum(b[i][k] * a[k][3] for k in range(3)) + b[i][3],)
        for i in range(3))


def color_matrix(color: dict | None) -> tuple:
    """3x4 affine matrix (rows R', G', B'; columns r, g, b, offset) for a clip's look:
    the preset's steps, then the sliders (brightness x(1+b), contrast c, saturation s)."""
    if not isinstance(color, dict):
        return _IDENTITY
    m = _IDENTITY
    for step in _color_presets().get(str(color.get("preset", "none")).lower(), []):
        m = _then(m, _op_matrix(str(step[0]), float(step[1])))
    b = max(-0.4, min(0.4, float(color.get("b", 0) or 0)))
    c = max(0.5, min(1.8, float(color.get("c", 1) or 1)))
    s = max(0.0, min(2.0, float(color.get("s", 1) or 1)))
    for op, v, neutral in (("brightness", 1 + b, 1.0), ("contrast", c, 1.0), ("saturate", s, 1.0)):
        if abs(v - neutral) > 0.005:
            m = _then(m, _op_matrix(op, v))
    return m


def _write_cube(m: tuple, path: Path, n: int = 17) -> Path:
    """The matrix as an n^3 .cube LUT for ffmpeg's lut3d. An affine map is reproduced
    exactly by trilinear interpolation, and the result is clamped once at the end - as the
    SVG filter does - rather than between chained filters (which clipped highlights)."""
    rows = [f"LUT_3D_SIZE {n}"]
    for bi in range(n):                                   # .cube order: red varies fastest
        for gi in range(n):
            for ri in range(n):
                v = (ri / (n - 1), gi / (n - 1), bi / (n - 1))
                rows.append(" ".join(
                    f"{max(0.0, min(1.0, row[0] * v[0] + row[1] * v[1] + row[2] * v[2] + row[3])):.6f}"
                    for row in m))
    path.write_text("\n".join(rows) + "\n", encoding="ascii")
    return path


def _color_chain(color: dict | None, cube: Path | None = None) -> str:
    """ffmpeg filter fragment for a colour look; '' when nothing is set. Needs a `cube`
    path to write the LUT to whenever there is something to apply."""
    m = color_matrix(color)
    if m == _IDENTITY or cube is None:
        return ""
    _write_cube(m, cube)
    path = cube.resolve().as_posix().replace(":", "\\:")   # a Windows drive colon splits filter args
    return f"lut3d=file='{path}':interp=trilinear"


def cut_clip(video: Path, start: float, end: float, out: Path, vertical: bool,
             subs: Path | None = None, crop: dict | float | None = None,
             loudnorm: bool = False, color: dict | None = None) -> None:
    if isinstance(crop, (int, float)):
        crop = {"px": float(crop)}
    crop = crop or {}
    mode = crop.get("mode", "crop") if vertical else None
    sub_chain = f",subtitles={subs.name}" if subs is not None else ""
    cwd = str(subs.parent) if subs is not None else None
    out_arg = str(out.resolve()) if subs is not None else str(out)
    col = _color_chain(color, out.with_suffix(".cube"))
    col_vf = f",{col}" if col else ""                 # colour grade goes before subtitles, never on the text

    fc = None                                     # -filter_complex string (fill mode only)
    if not vertical:
        vf = r"scale=-2:min(1080\,ih)" + col_vf + sub_chain   # only downscale, never upscale
    elif mode in ("fill", "cropfill"):            # blurred copy fills the bars; foreground = whole frame or a crop
        blur = max(2.0, min(80.0, float(crop.get("blur", 24))))
        sub_fc = f";[v]subtitles={subs.name}[vout]" if subs is not None else ""
        map_v = "[vout]" if subs is not None else "[v]"
        bg = (f"[b]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              # x0.94 like the preview's brightness(.94); eq's brightness would *subtract*
              f"gblur=sigma={blur:.1f},colorchannelmixer=rr=0.94:gg=0.94:bb=0.94,"
              f"eq=saturation=1.12[bg]")
        col_fg = f",{col}" if col else ""             # grade the framed subject, leave the blurred bars alone
        if mode == "cropfill":                    # subject you framed, blurred fill behind it
            z = max(1.0, min(4.0, float(crop.get("zoom", 1.0))))
            px = max(0.0, min(1.0, float(crop.get("px", 0.5))))
            py = max(0.0, min(1.0, float(crop.get("py", 0.5))))
            ar = _aspect_ratio(crop.get("aspect", "16/9"))
            bh = f"ih/{z:.4f}"
            bw = f"ih*{ar:.5f}/{z:.4f}"
            cx, cy = _follow_xy(crop, start, px, py)
            fg = (f"[a]crop=w={bw}:h={bh}:x={cx}:y={cy},"
                  f"scale=1080:-2:flags=lanczos{col_fg}[fg]")
        else:
            fg = f"[a]scale=1080:-2:flags=lanczos{col_fg}[fg]"
        fc = f"[0:v]split=2[a][b];{bg};{fg};[bg][fg]overlay=(W-w)/2:(H-h)/2[v]" + sub_fc
        vf = None
    else:                                         # crop / manual reframe (px/py travel, zoom punch-in)
        z = max(1.0, min(4.0, float(crop.get("zoom", 1.0))))
        px = max(0.0, min(1.0, float(crop.get("px", 0.5))))
        py = max(0.0, min(1.0, float(crop.get("py", 0.5))))
        bw = f"ih*9/(16*{z:.4f})"
        bh = f"ih/{z:.4f}"
        cx, cy = _follow_xy(crop, start, px, py)
        vf = (f"crop=w={bw}:h={bh}:x={cx}:y={cy},"
              "scale=1080:1920:flags=lanczos" + col_vf + sub_chain)

    cmd = [FFMPEG, "-y", *_thread_args(), *_filter_thread_args(fc is not None),
           "-ss", f"{start:.2f}", "-i", str(video), "-t", f"{end - start:.2f}"]
    if fc is not None:
        cmd += ["-filter_complex", fc, "-map", map_v, "-map", "0:a?"]
    else:
        cmd += ["-vf", vf]
    # yuv420p: a 10-bit source (phone HEVC / HDR) otherwise encodes to High 10 profile,
    # which Safari, many Android devices and the social uploaders all refuse — the clip
    # downloads fine and then won't play, with nothing logged anywhere.
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", *_thread_args()]
    if loudnorm:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_arg]
    run(cmd, cwd=cwd)


def mix_sfx(clip: Path, sfx: list[dict], sfx_dir: Path, out: Path) -> bool:
    """Mix point sound-effects onto a rendered clip's audio track (video copied through).

    `sfx`: [{"file": "<name under sfx_dir>", "at": <clip-local seconds>, "gain": <dB>}].
    Returns True if a mix was written, False if there was nothing valid to do.
    """
    items = []
    for s in sfx or []:
        fn = str(s.get("file") or "")
        if not fn or Path(fn).name != fn:
            continue
        p = sfx_dir / fn
        if p.is_file():
            items.append((p, max(0.0, float(s.get("at", 0.0))), float(s.get("gain", 0.0))))
    if not items:
        return False

    inputs: list[str] = ["-i", str(clip)]
    for p, _, _ in items:
        inputs += ["-i", str(p)]

    parts, labels = [], ["[0:a]"]
    for i, (_p, at, gain) in enumerate(items, 1):
        d = int(round(at * 1000))
        vol = 10 ** (gain / 20)
        parts.append(f"[{i}:a]aformat=channel_layouts=stereo,adelay={d}|{d},volume={vol:.3f}[s{i}]")
        labels.append(f"[s{i}]")
    fc = (";".join(parts) + ";" + "".join(labels)
          + f"amix=inputs={len(labels)}:duration=first:normalize=0:dropout_transition=0,"
            "alimiter=limit=0.97[a]")
    run([FFMPEG, "-y", *_thread_args(), *inputs, "-filter_complex", fc,
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(out)])
    return True


def grab_frame(video: Path, at: float, out: Path) -> None:
    run([FFMPEG, "-y", *_thread_args(), "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=640:-2", str(out)])


# ------------------------------------------------------------------ transcription (Groq Whisper)

_ONOMATOPOEIA = {"ah", "ahh", "ahhh", "aaa", "aaah", "argh", "agh", "ugh", "grr", "rah",
                 "woo", "wooo", "woah", "whoa", "aah", "ooh", "oooh", "yee", "eee", "mmm"}


def _is_noise_token(t: str) -> bool:
    return bool(t in _ONOMATOPOEIA
               or re.fullmatch(r"[a-z]?(.)\1{2,}", t)        # "hhhh", "aaaa", "woooo", "brrr"
               or re.fullmatch(r"[aeiouhrg]{3,}", t))        # "ahhhh", "urgh", "aargh"


def _segment_action(s: dict, cfg: dict) -> str | None:
    """Rule-based triage for a Whisper segment. Returns "drop", "compress", or None.
    Uses only fields Whisper already returns - no ML."""
    text = (s.get("text") or "").strip()
    toks = [t for t in (re.sub(r"[^\w']", "", w.lower()) for w in text.split()) if t]
    dur = float(s.get("end", 0)) - float(s.get("start", 0))
    if not toks:
        return "drop:empty"
    if float(s.get("avg_logprob", 0.0)) < cfg["min_avg_logprob"]:
        return "drop:low-confidence"
    if float(s.get("compression_ratio", 0.0)) > cfg["max_compression_ratio"]:
        return "drop:repetitive"
    if float(s.get("no_speech_prob", 0.0)) > 0.7:
        return "drop:no-speech"
    if len(toks) >= 4 and len(set(toks)) <= 2:
        return "drop:single-word-repeat"                      # "ARGH ARGH ARGH ..."
    if dur > 5.0 and len(toks) < 4:
        if all(_is_noise_token(t) for t in toks):
            return "drop:held-noise"                          # a stretched "ahhh"
        return "compress:held-line"                           # a stretched real line, e.g. "I'm gay!"
    return None


def transcribe(mp3: Path, workdir: Path, cfg: dict) -> list[dict]:
    import groq

    client = groq.Groq()
    want_words = bool(cfg.get("word_timestamps"))
    segments: list[dict] = []
    all_words: list[dict] = []
    dropped: dict[str, int] = {}
    chunks = split_audio(mp3, workdir)
    for n, (part, offset) in enumerate(chunks, 1):
        if len(chunks) > 1:
            print(f"    transcribing chunk {n}/{len(chunks)} ...", flush=True)
        kwargs = dict(file=(part.name, part.read_bytes()), model=GROQ_WHISPER,
                      response_format="verbose_json", language="en")
        if want_words:
            kwargs["timestamp_granularities"] = ["segment", "word"]
        for attempt in range(4):
            try:
                data = client.audio.transcriptions.create(**kwargs)
                break
            except (groq.RateLimitError, groq.APIStatusError) as e:
                if getattr(e, "status_code", None) not in (429, 413) or attempt == 3:
                    raise
                wait = _retry_wait(e, GROQ_WHISPER)
                print(f"    whisper rate limited - waiting {wait}s ...", flush=True)
                time.sleep(wait)
        data = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        for w in data.get("words") or []:
            wd = (w.get("word") or "").strip()
            if wd:
                all_words.append({"w": wd, "start": float(w["start"]) + offset,
                                  "end": float(w["end"]) + offset})
        for s in data.get("segments", []):
            text = (s.get("text") or "").strip()
            if not text:
                continue
            start, end = float(s["start"]) + offset, float(s["end"]) + offset
            if cfg.get("filter_hallucinations", True):
                act = _segment_action(s, cfg)
                if act and act.startswith("drop"):
                    dropped[act.split(":", 1)[1]] = dropped.get(act.split(":", 1)[1], 0) + 1
                    continue
                if act and act.startswith("compress"):
                    end = min(end, start + 6.0)          # keep the words, trim the long held tail
                    dropped["shortened-held-line"] = dropped.get("shortened-held-line", 0) + 1
            segments.append({"start": start, "end": end, "text": text,
                             "conf": round(float(s.get("avg_logprob") or 0.0), 3)})
    segments.sort(key=lambda s: s["start"])
    if all_words:
        all_words.sort(key=lambda w: w["start"])
        used = [False] * len(all_words)
        for seg in segments:                                 # assign each word to exactly one segment
            seg["words"] = []
            for i, w in enumerate(all_words):
                if used[i]:
                    continue
                mid = (w["start"] + w["end"]) / 2
                if seg["start"] - 0.15 <= mid <= seg["end"] + 0.15:
                    seg["words"].append(w)
                    used[i] = True
    if dropped:
        print("        dropped noisy segments:",
              ", ".join(f"{v} {k}" for k, v in sorted(dropped.items())), flush=True)
    return segments


def as_timeline(segments: list[dict]) -> str:
    rows = []
    for s in segments:
        mm, ss = divmod(int(s["start"]), 60)
        rows.append(f"[{mm:02d}:{ss:02d} | {s['start']:.1f}s] {s['text']}")
    return "\n".join(rows)


# ------------------------------------------------------------------ clip model

class Clip(BaseModel):
    start_seconds: float
    end_seconds: float
    title: str = ""
    hook: str = Field(default="", description="the exact opening words of the clip, copied "
                                              "verbatim from the transcript - NOT a description")
    why: str = ""
    quote: str = ""
    score: float = 5.0

    beat_type: str = "moment"           # reaction|bit|story|quote|fail|wholesome|moment
    hook_line: str = ""                 # verbatim first line of the beat (boundary snap)
    payoff_line: str = ""               # verbatim payoff / last line of the beat
    arc: dict = Field(default_factory=dict)   # {"hook":0-3,"arc":0-3,"quote":0-2,"standalone":0-2,"trend":0-2}

    _energy: dict = PrivateAttr(default_factory=dict)   # measured loudness stats for this clip
    _rank_pct: float | None = PrivateAttr(default=None)  # verify's forced rank: 1.0 best … 0.0 worst
    _auto: dict = PrivateAttr(default_factory=dict)     # suggested edit (reframe / caption / colour / sfx)

    @field_validator("score", mode="before")
    @classmethod
    def _normalise_score(cls, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 5.0
        if v > 10:                              # some models answer on a 0-100 scale
            v /= 10.0
        return max(1.0, min(10.0, v))

    @field_validator("title", "hook", "why", "quote", "beat_type", "hook_line", "payoff_line",
                     mode="before")
    @classmethod
    def _stringify(cls, v):
        return "" if v is None else str(v)


class ClipSet(BaseModel):
    clips: list[Clip]


PICKER_BRIEF = (
    "You are a senior short-form video editor. From a long streamer compilation you pull "
    "the individual moments that work best as standalone YouTube Shorts / TikToks / Reels.\n"
    "A great clip: hook in the first 2 seconds, clear setup and payoff, a strong reaction "
    "or a quotable line, zero outside context needed. 15-45s is the sweet spot; never "
    "below 10s or above 75s. Clips must not overlap. Favour moments a viewer rewatches or "
    "sends to a friend over merely 'interesting' ones.\n"
    "The best clips are almost always where the ENERGY SPIKES - a sudden loud reaction, a "
    "shout, an outburst, a group losing it - with a quotable line right before or after the "
    "spike. Skip calm, level-toned explaining even when the words are fine.\n"
    "HARD RULES:\n"
    "- Every clip MUST contain a real spoken line you can put in the `quote` field. Never "
    "pick a stretch where the transcript is empty or near-empty (screaming, silence, music) "
    "- you cannot see the video, only the words.\n"
    "- start_seconds must sit on the setup line, not on the punchline - include the build-up.\n"
    "- One moment per clip. If the speaker jumps to a different topic or a different bit, "
    "END the clip before the jump - do not staple two moments together.\n"
    "- Ignore obviously garbled or repeated-nonsense lines in the transcript; build the clip "
    "around the coherent dialogue only.\n"
    "- Keep clips tight around the moment; do not pad with unrelated talk before or after.\n"
    "- `score` (1-10) should reward: instant hook, a spike in energy near the payoff, a line "
    "worth quoting, and zero setup needed. A calm-but-clear moment tops out around 6."
)


# ---- audio energy: an EBU R128 loudness envelope, ~1 value / second ----------

def loudness_track(audio: Path) -> list[float]:
    """One RMS-level value (dB) per second of audio via ffmpeg astats. Louder = higher
    (roughly -20 = loud speech/hype, -40 = quiet). [] on any failure — degrade gracefully."""
    try:
        p = subprocess.run(
            [FFMPEG, "-hide_banner", "-v", "info", *_thread_args(), "-i", str(audio),
             "-af", "aresample=8000,asetnsamples=8000:p=0,astats=metadata=1:reset=1,"
                    "ametadata=print:key=lavfi.astats.Overall.RMS_level",
             "-f", "null", "-"],
            capture_output=True, text=True,
        )
    except Exception:
        return []
    out = []
    for m in re.finditer(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-?inf)", p.stderr):
        v = m.group(1)
        out.append(-90.0 if "inf" in v else max(-90.0, float(v)))
    return out


def _loud_stats(loud: list[float]) -> dict:
    real = sorted(x for x in loud if x > -60.0)
    if len(real) < 8:
        return {}
    def q(f: float) -> float:
        return real[min(len(real) - 1, int(f * len(real)))]

    return {"p40": q(0.40), "p75": q(0.75), "p92": q(0.92)}


def _clip_energy(c: "Clip", loud: list[float]) -> dict:
    """peak / payoff loudness for a clip, plus the offset of the loudest second."""
    if not loud:
        return {}
    a = max(0, int(c.start_seconds))
    b = min(len(loud) - 1, int(c.end_seconds))
    if b <= a:
        return {}
    win = loud[a:b + 1]
    real = [x for x in win if x > -60.0]
    if not real:
        return {}
    peak = max(win)
    peak_at = a + win.index(peak)
    half = len(win) // 2
    payoff = max((x for x in win[half:] if x > -60.0), default=peak)
    return {"peak": peak, "payoff": payoff, "peak_at": peak_at,
            "loud_frac": round(sum(x > -60.0 for x in win) / len(win), 2)}


# ---- boundary snapping (kills mid-sentence cuts) -----------------------------

def _boundaries(segments: list[dict]) -> tuple[list[float], list[float], list[float]]:
    """(segment starts, segment ends, pause points). Pauses are the best cut points."""
    starts = sorted({round(s["start"], 2) for s in segments})
    ends = sorted({round(s["end"], 2) for s in segments})
    pauses = set()
    for a, b in zip(segments, segments[1:]):
        if b["start"] - a["end"] > 0.35:
            pauses.add(round(a["end"], 2))
            pauses.add(round(b["start"], 2))
    return starts, ends, sorted(pauses)


def _snap(value: float, points, max_shift: float) -> float:
    near = [p for p in points if abs(p - value) <= max_shift]
    return min(near, key=lambda p: abs(p - value)) if near else value


def _snap_clip(c: Clip, segments: list[dict], cfg: dict) -> None:
    """Pull each edge to a clean boundary. If an edge lands inside a spoken segment,
    grow the clip to that whole sentence rather than slicing it - a mid-word cut is
    worse than a bit of extra context."""
    starts, ends, pauses = _boundaries(segments)
    ms = cfg["snap_max_shift"]

    inside = next((s for s in segments if s["start"] - 0.2 <= c.start_seconds < s["end"] - 0.2), None)
    if inside and inside["start"] >= c.start_seconds - ms:
        c.start_seconds = inside["start"]                       # back up to the sentence start
    else:
        c.start_seconds = _snap(c.start_seconds, sorted(set(pauses) | set(starts)), ms)

    inside = next((s for s in segments if s["start"] + 0.2 < c.end_seconds <= s["end"] + 0.2), None)
    if inside and inside["end"] <= c.end_seconds + ms:
        c.end_seconds = inside["end"]                           # extend to the sentence end
    else:
        c.end_seconds = _snap(c.end_seconds, sorted(set(pauses) | set(ends)), ms)

    c.start_seconds = max(0.0, c.start_seconds - cfg["lead_seconds"])
    c.end_seconds = c.end_seconds + cfg["tail_seconds"]


def _speech_span(c: Clip, segments: list[dict]):
    """(first_word_start, last_word_end, word_count) for transcript overlapping the clip."""
    hits = [s for s in segments if s["start"] < c.end_seconds and s["end"] > c.start_seconds]
    if not hits:
        return None
    return (min(s["start"] for s in hits), max(s["end"] for s in hits),
            sum(len(s["text"].split()) for s in hits))


def _quiet(a: float, b: float, loud: list[float] | None) -> bool:
    """True when [a, b] is genuinely dead air - below the video's median loudness. A gap in
    the *speech* full of chewing, gasping, laughing or music is part of the moment."""
    stats = _loud_stats(loud or [])
    if not stats:
        return True                                   # no loudness data: old behaviour
    win = [x for x in (loud or [])[max(0, int(a)):max(int(a) + 1, int(b))] if x > -60.0]
    return not win or sorted(win)[len(win) // 2] < stats["p40"]


def _trim_to_dense_block(c: Clip, segments: list[dict], cfg: dict,
                         loud: list[float] | None = None) -> None:
    """If a silent gap wider than max_gap_seconds sits inside the clip, keep only the
    contiguous speech block that carries the most words - the rest is another moment.
    Only quiet gaps split: a loud stretch without words (eating, reacting) stays in."""
    hits = sorted((s for s in segments if s["start"] < c.end_seconds and s["end"] > c.start_seconds),
                  key=lambda s: s["start"])
    if len(hits) < 2:
        return
    blocks, cur = [], [hits[0]]
    for prev, s in zip(hits, hits[1:]):
        if s["start"] - prev["end"] > cfg["max_gap_seconds"] and _quiet(prev["end"], s["start"], loud):
            blocks.append(cur)
            cur = [s]
        else:
            cur.append(s)
    blocks.append(cur)
    real = [b for b in blocks if sum(len(x["text"].split()) for x in b) >= 3]
    if len(real) < 2:                    # tiny trailing fragments are not "another moment"
        return
    best = max(real, key=lambda b: sum(len(x["text"].split()) for x in b))
    c.start_seconds = max(c.start_seconds, best[0]["start"] - cfg["lead_seconds"] - 0.5)
    c.end_seconds = min(c.end_seconds, best[-1]["end"] + cfg["tail_seconds"] + 0.5)


def _grow_to_payoff(c: Clip, segments: list[dict], cfg: dict) -> None:
    """Weak pickers often stop on the setup line. If the clip is short, pull the end
    forward through speech that keeps flowing (no real gap), up to +12s / max_seconds -
    so the punchline isn't left out."""
    if c.end_seconds - c.start_seconds >= 18.0:
        return                                            # already long - trust the picker
    limit = min(c.start_seconds + cfg["max_seconds"], c.end_seconds + 12.0)
    cur = c.end_seconds
    for _ in range(6):
        nxt = min((s for s in segments
                   if cur - 0.3 <= s["start"] <= cur + 3.0 and s["end"] > cur),   # only through a tight run
                  key=lambda s: s["start"], default=None)
        if not nxt or nxt["end"] > limit:
            break
        cur = nxt["end"]
    c.end_seconds = max(c.end_seconds, cur)


def _trim_silence(c: Clip, segments: list[dict], cfg: dict) -> None:
    """Pull the edges in to where speech actually is - kill trailing/leading dead air."""
    sp = _speech_span(c, segments)
    if not sp:
        return
    first, last, _ = sp
    c.start_seconds = max(c.start_seconds, first - cfg["lead_seconds"] - 1.0)
    c.end_seconds = min(c.end_seconds, last + cfg["tail_seconds"] + 2.0)


def _clamp_clip(c: Clip, dur: float, cfg: dict, segments: list[dict] | None = None) -> None:
    lo, hi = cfg["min_seconds"], cfg["max_seconds"]
    c.start_seconds = max(0.0, c.start_seconds)
    if dur:
        c.end_seconds = min(c.end_seconds, dur)
    length = c.end_seconds - c.start_seconds
    if length > hi:
        c.end_seconds = c.start_seconds + hi
    elif length < lo:
        # Too short: grow FORWARD first. Pickers land on the punchline, so what's missing is
        # usually the reaction and aftermath, not more build-up; padding the start backwards
        # (the old order) made 10 s clips that were mostly lead-in. End on a sentence end
        # when one is close, never mid-word.
        target = c.start_seconds + lo
        if segments:
            cut = next((s for s in segments if s["start"] < target < s["end"]), None)
            if cut and cut["end"] - c.start_seconds <= hi:
                target = cut["end"]
        c.end_seconds = min(max(c.end_seconds, target), dur or target)
        short = lo - (c.end_seconds - c.start_seconds)
        if short > 0:                                   # hit the end of the video: reach back
            c.start_seconds = max(0.0, c.start_seconds - short)


def _end_on_sentence(c: Clip, segments: list[dict], cfg: dict, dur: float) -> None:
    """Last word: if the end sits inside a spoken line, finish the line (when that stays under
    max_seconds and within snap_max_shift), else stop before it - as long as the clip keeps
    min_seconds. Runs after every step that can move the end (verify, clamp)."""
    inside = next((s for s in segments if s["start"] + 0.15 < c.end_seconds < s["end"] - 0.15), None)
    if not inside:
        return
    lo, hi = cfg["min_seconds"], cfg["max_seconds"]
    ext = inside["end"] + 0.3
    if ext - c.end_seconds <= cfg["snap_max_shift"] and ext - c.start_seconds <= hi:
        c.end_seconds = min(ext, dur or ext)
    elif inside["start"] - c.start_seconds >= lo:
        c.end_seconds = inside["start"]
    elif ext - c.start_seconds <= hi:                      # can't shorten; finishing the line wins
        c.end_seconds = min(ext, dur or ext)


def _nonoverlap(clips: list[Clip], k: int) -> list[Clip]:
    kept: list[Clip] = []
    for c in clips:
        if any(c.start_seconds < x.end_seconds and c.end_seconds > x.start_seconds for x in kept):
            continue
        kept.append(c)
        if len(kept) >= k:
            break
    return kept


MIN_CLIPS = 4                                       # never hand back fewer than this (if the pool allows)


def _finalize(clips: list[Clip], segments: list[dict], n: int, cfg: dict,
              loud: list[float] | None = None) -> list[Clip]:
    dur = segments[-1]["end"] if segments else 0.0
    hard_min = min(cfg["min_seconds"] - 2.0, 8.0)
    n = max(int(n or 0), int(cfg.get("min_clips", MIN_CLIPS)))   # a user-chosen count lowers the floor
    keep_n = min(round(n * 1.6), n + 4)   # hand stage 2 a slightly fuller pool; it re-ranks to n
    stats = _loud_stats(loud or [])
    good: list[Clip] = []
    spare: list[Clip] = []                          # passed length but failed the sparse test — fallback pool
    for c in clips:
        if c.end_seconds - c.start_seconds < 1.0:   # a degenerate pick (seen: start == end)
            continue
        _snap_clip(c, segments, cfg)
        _grow_to_payoff(c, segments, cfg)           # don't leave the punchline just past the end
        _trim_to_dense_block(c, segments, cfg, loud)  # split off other moments across a quiet gap
        _trim_silence(c, segments, cfg)             # shave leading / trailing dead air
        _clamp_clip(c, dur, cfg, segments)
        _end_on_sentence(c, segments, cfg, dur)
        sp = _speech_span(c, segments)
        words = sp[2] if sp else 0
        length = max(c.end_seconds - c.start_seconds, 0.1)
        if length < hard_min:                       # gap-trim left only a fragment
            continue
        # audio-energy adjustment: mostly a boost for a loud reaction near the payoff,
        # only a light touch down for a genuinely flat moment
        if stats:
            en = _clip_energy(c, loud)
            if en:
                span = max(stats["p92"] - stats["p40"], 3.0)
                lift = (en["payoff"] - stats["p40"]) / span      # ~0 at median, ~1 at a real spike
                boost = max(-0.35, min(1.6, (lift - 0.4) * 2.0)) * float(cfg.get("energy_weight", 1.0))
                c.score = max(1.0, min(10.0, c.score + boost))
                c._energy = en
        sparse = words < cfg["min_words"] or words / length < 0.35
        if sparse and c.score < 9.5:                # montage / dead-air / near-silent
            spare.append(c)
            continue
        good.append(c)
    good.sort(key=lambda c: (-c.score, c.start_seconds))
    kept = _nonoverlap(good, keep_n)
    if len(kept) < MIN_CLIPS and spare:             # backfill from the near-misses to reach the floor
        spare.sort(key=lambda c: -c.score)
        kept = _nonoverlap(kept + spare, max(keep_n, MIN_CLIPS))
    kept.sort(key=lambda c: c.start_seconds)
    return kept


# ---- auto edit: sound + effects suggestion per clip, from measured energy ----

def attach_auto(clips: list["Clip"], loud: list[float] | None, category: str = "",
                hot_threshold: float | None = None) -> None:
    """Set c._auto for each clip. Called AFTER stage-2 refinement so it sees final bounds.
    `category` (from detect_category) nudges the energy threshold — see _auto_edit."""
    stats = _loud_stats(loud or [])
    for c in clips:
        en = _clip_energy(c, loud) if loud else {}
        c._energy = en or {}
        c._auto = _auto_edit(c, c._energy, stats, category, hot_threshold)

# genres that run hot by default get a lower bar to call a moment "hot"; calmer talk genres
# need a clearer spike before we commit to punchy styling. Unlisted/"general" keeps 0.45.
_HOT_THRESHOLD = {"gaming": 0.35, "reaction": 0.35, "comedy": 0.35, "sports": 0.35,
                   "podcast": 0.55, "interview": 0.55, "tutorial": 0.55}


def _auto_edit(c: "Clip", en: dict, stats: dict, category: str = "",
               hot_threshold: float | None = None) -> dict:
    """A ready-to-apply edit for one clip: 9:16 reframe, caption style/anim, colour, SFX hits.
    Purely heuristic from the loudness envelope (+ the video's detected category) — no extra
    model call."""
    length = max(c.end_seconds - c.start_seconds, 1.0)
    hot = False
    if stats and en:
        span = max(stats["p92"] - stats["p40"], 3.0)
        thresh = hot_threshold if hot_threshold is not None else _HOT_THRESHOLD.get(category, 0.45)
        hot = (en.get("payoff", -40) - stats["p40"]) / span > thresh    # a genuine reaction spike

    sfx = [{"file": "whoosh.m4a", "at": 0.0, "gain": -4 if hot else -7}]
    if en.get("peak_at") is not None:
        rel = round(en["peak_at"] - c.start_seconds, 2)
        if 0.25 < rel < length - 0.25:
            sfx.append({"file": "impact.m4a" if hot else "ding.m4a",
                        "at": rel, "gain": -4 if hot else -9})

    return {
        "vertical": True,
        # zoom 1: show the whole frame. The old 1.05/1.12 punch-in silently trimmed the
        # sides of every clip, which read as "the full video got cropped".
        "crop": {"mode": "cropfill", "aspect": "16/9", "px": 0.5, "py": 0.5,
                 "zoom": 1.0, "blur": 22},
        "caption": {"style": "bold" if hot else "clean",
                    "anim": "pop" if hot else "fade", "karaoke": hot},
        "color": {"preset": "punchy" if hot else "none", "b": 0, "c": 1, "s": 1},
        "sfx": sfx,
        "hot": hot,
    }


# ---- sight: where the face is, so the 9:16 crop lands on the person ----------

FACE_FPS = 2.0          # face samples per second of clip
FACE_DEADZONE = 0.06    # the virtual camera ignores face moves smaller than this (frame widths)
FACE_EASE = 0.35        # how fast the camera catches up once the face leaves the dead zone
TRACK_MAX_POINTS = 120  # keyframes kept for the render's crop expression (fast-cut videos jump a lot)
SCENE_THRESHOLD = 0.4   # ffmpeg scene score that counts as a cut (0.3 caught soft pans too)
SCENE_MERGE = 0.3       # cuts closer than this are one transition (flash frames, dissolves)


def _scene_cuts(video: Path, start: float, dur: float) -> list[float]:
    """Shot changes inside [start, start+dur], in source seconds. One fast decode of a
    160px copy through ffmpeg's scene score. [] on any failure - tracking then behaves
    as one continuous shot, as before."""
    try:
        p = subprocess.run(
            [FFMPEG, "-hide_banner", "-v", "info", *_thread_args(), "-ss", f"{start:.2f}",
             "-t", f"{dur:.2f}", "-i", str(video), "-vf",
             f"scale=160:-2,select='gt(scene,{SCENE_THRESHOLD})',metadata=print",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True)
    except Exception:  # noqa: BLE001
        return []
    cuts: list[float] = []
    for m in re.finditer(r"pts_time:([\d.]+)", p.stderr):
        t = start + float(m.group(1))
        if not cuts or t - cuts[-1] > SCENE_MERGE:
            cuts.append(round(t, 2))
    return cuts


YUNET_MODEL = HERE / "models" / "face_detection_yunet_2023mar.onnx"


def _face_detector():
    """(detect(img) -> [(x, y, w, h), ...], name). YuNet - a small CNN that also finds
    turned heads, small and badly lit faces - when its model file is present; else OpenCV's
    Haar cascade, which only sees faces looking at the camera. None if OpenCV is missing."""
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return None
    if YUNET_MODEL.exists() and hasattr(cv2, "FaceDetectorYN"):
        try:
            yn = cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (320, 320), 0.6, 0.3, 50)

            def detect_yunet(img):
                h, w = img.shape[:2]
                yn.setInputSize((w, h))
                _, faces = yn.detect(img)
                return [] if faces is None else [tuple(float(v) for v in f[:4]) for f in faces]
            return detect_yunet, "yunet"
        except Exception:  # noqa: BLE001 - a broken model file must not break analysis
            pass
    haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if haar.empty():
        return None

    def detect_haar(img):
        w = img.shape[1]
        gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        return [tuple(map(float, r)) for r in haar.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=6, minSize=(max(24, w // 20), max(24, w // 20)))]
    return detect_haar, "haar"


def face_centres(video: Path, clips: list["Clip"], workdir: Path) -> dict[int, dict]:
    """{clip index: {"x","y" (0-1, median), "size", "seen", "ar", "track"}} for clips where a
    face shows in at least ~40% of frames sampled at FACE_FPS. `track` is a smoothed camera
    path [[source_seconds, x, y], ...] the render's crop follows; x/y are the median for a
    static frame. Largest face per frame, so a passer-by doesn't pull the crop.
    {} if OpenCV is unavailable - framing then falls back to centre, never blocks analysis."""
    found = _face_detector()
    if found is None:
        return {}
    import cv2
    detect, _name = found
    out: dict[int, dict] = {}
    for i, c in enumerate(clips):
        d = workdir / f"faces_{i}"
        d.mkdir(exist_ok=True)
        dur = max(1.0, c.end_seconds - c.start_seconds)
        try:
            run([FFMPEG, "-y", "-v", "error", *_thread_args(), "-ss", f"{c.start_seconds:.2f}",
                 "-t", f"{dur:.2f}", "-i", str(video), "-vf", f"fps={FACE_FPS},scale=480:-2",
                 str(d / "f_%03d.jpg")])
        except RuntimeError:
            continue
        times, xs, ys, sizes, n, ar = [], [], [], [], 0, 16 / 9
        for k, f in enumerate(sorted(d.glob("f_*.jpg"))):
            img = cv2.imread(str(f))
            if img is None:
                continue
            n += 1
            h, w = img.shape[:2]
            ar = w / h
            faces = detect(img)
            times.append(c.start_seconds + (k + 0.5) / FACE_FPS)   # fps filter samples mid-interval
            if len(faces):
                x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])
                xs.append((x + fw / 2) / w)
                ys.append((y + fh / 2) / h)
                sizes.append(fw / w)
            else:
                xs.append(None)
                ys.append(None)
        seen = sum(v is not None for v in xs)
        if not n or seen < max(2, round(0.4 * n)):
            continue
        med = lambda vals: float(sorted(vals)[len(vals) // 2])  # noqa: E731 - plain floats for JSON
        out[i] = {"x": round(med([v for v in xs if v is not None]), 3),
                  "y": round(med([v for v in ys if v is not None]), 3),
                  "size": round(med(sizes), 3), "seen": round(seen / n, 2), "ar": round(ar, 4),
                  "track": _camera_path(times, xs, ys, _scene_cuts(video, c.start_seconds, dur))}
    return out


def _camera_path(times: list[float], xs: list, ys: list, cuts: list[float] | tuple = ()) -> list[list[float]]:
    """Turn raw per-frame face centres into a calm virtual-camera path, one shot at a time.

    Within a shot: gaps filled from the nearest detection -> 5-tap median (kills one-frame
    misdetections) -> dead zone + easing (the camera only moves when the face really leaves
    the centre, then glides) -> keyframes only where the path bends. At a shot change the
    camera JUMPS to the new shot's framing on the cut (a 10 ms ramp), instead of gliding
    across it as if the person had walked; a shot with no face holds the clip's usual
    position. No cuts = one shot, the original behaviour."""
    if not times:
        return []
    found_x = [v for v in xs if v is not None]
    found_y = [v for v in ys if v is not None]
    hold = (sorted(found_x)[len(found_x) // 2] if found_x else 0.5,
            sorted(found_y)[len(found_y) // 2] if found_y else 0.5)
    shot_of = [sum(1 for c in cuts if c <= t) for t in times]
    pts: list[list[float]] = []
    for shot in sorted(set(shot_of)):
        idx = [k for k, s in enumerate(shot_of) if s == shot]
        sx, sy = [xs[k] for k in idx], [ys[k] for k in idx]
        if all(v is None for v in sx):
            sx, sy = [hold[0]] * len(idx), [hold[1]] * len(idx)
        seg = _camera_path_one([times[k] for k in idx], sx, sy)
        if pts and shot > 0:
            cut_t = max((c for c in cuts if c <= times[idx[0]]), default=times[idx[0]])
            moved = abs(seg[0][1] - pts[-1][1]) > FACE_DEADZONE or abs(seg[0][2] - pts[-1][2]) > FACE_DEADZONE
            if moved:                                      # cut: hold to the cut, then jump
                pts.append([round(cut_t - 0.01, 2), pts[-1][1], pts[-1][2]])
                pts.append([round(cut_t, 2), seg[0][1], seg[0][2]])
                seg = [p for p in seg if p[0] > cut_t]
            else:                                          # same framing: no jolt across the cut
                seg = [[p[0], pts[-1][1], pts[-1][2]] if abs(p[1] - pts[-1][1]) <= FACE_DEADZONE else p
                       for p in seg]
        pts.extend(p for p in seg if not pts or p[0] > pts[-1][0])
    if len(pts) > TRACK_MAX_POINTS:                       # thin evenly, keep both ends
        step = (len(pts) - 1) / (TRACK_MAX_POINTS - 1)
        pts = [pts[round(j * step)] for j in range(TRACK_MAX_POINTS)]
    return pts


def _camera_path_one(times: list[float], xs: list, ys: list) -> list[list[float]]:
    """One shot's camera path (see _camera_path)."""
    def fill(vals):
        known = [(k, v) for k, v in enumerate(vals) if v is not None]
        out = []
        for k in range(len(vals)):
            if vals[k] is not None:
                out.append(vals[k])
                continue
            before = next((v for j, v in reversed(known) if j < k), None)
            after = next((v for j, v in known if j > k), None)
            out.append(before if after is None else after if before is None else (before + after) / 2)
        return out

    if len(times) == 1 or all(v is None for v in xs):
        x0 = next((v for v in xs if v is not None), 0.5)
        y0 = next((v for v in ys if v is not None), 0.5)
        return [[round(times[0], 2), round(x0, 3), round(y0, 3)]]

    def median5(vals):
        return [sorted(vals[max(0, k - 2):k + 3])[len(vals[max(0, k - 2):k + 3]) // 2] for k in range(len(vals))]

    def camera(vals):
        cam, path = vals[0], []
        for v in vals:
            gap = v - cam
            if abs(gap) > FACE_DEADZONE:
                cam += (gap - FACE_DEADZONE * (1 if gap > 0 else -1)) * FACE_EASE
            path.append(cam)
        return path

    cx, cy = camera(median5(fill(xs))), camera(median5(fill(ys)))
    pts = [[round(times[0], 2), round(cx[0], 3), round(cy[0], 3)]]
    for k in range(1, len(times)):
        if abs(cx[k] - pts[-1][1]) > 0.004 or abs(cy[k] - pts[-1][2]) > 0.004 or k == len(times) - 1:
            pts.append([round(times[k], 2), round(cx[k], 3), round(cy[k], 3)])
    return pts


def _track_expr(track: list, start: float, axis: int) -> str | None:
    """Piecewise-linear ffmpeg expression of the face position (0-1) over clip time `t`,
    from a camera path in source seconds. Commas escaped for use inside a filtergraph."""
    pts = []
    for p in (track or [])[:TRACK_MAX_POINTS * 2]:
        try:
            pts.append((float(p[0]) - start, max(0.0, min(1.0, float(p[axis])))))
        except (TypeError, ValueError, IndexError):
            continue
    pts.sort()
    if len(pts) < 2:
        return None
    expr = f"{pts[-1][1]:.4f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(pts, pts[1:]))):
        seg = f"{v0:.4f}+({v1 - v0:.4f})*(t-{t0:.3f})/{max(t1 - t0, 1e-3):.3f}"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    expr = f"if(lt(t,{pts[0][0]:.3f}),{pts[0][1]:.4f},{expr})"
    return expr.replace(",", "\\,")


def _follow_xy(crop: dict, start: float, px: float, py: float) -> tuple[str, str]:
    """crop filter x/y: centre the window on the tracked face when a track is given,
    else the static px/py placement."""
    fx = _track_expr(crop.get("track"), start, 1)
    fy = _track_expr(crop.get("track"), start, 2)
    x = f"max(0\\,min(iw-ow\\,({fx})*iw-ow/2))" if fx else f"(iw-ow)*{px:.4f}"
    y = f"max(0\\,min(ih-oh\\,({fy})*ih-oh/2))" if fy else f"(ih-oh)*{py:.4f}"
    return x, y


def frame_on(face: dict, mode: str = "crop", zoom: float = 1.0) -> dict:
    """px/py that centre cut_clip's crop window on the face (inverse of x=(iw-w)*px)."""
    ar = float(face.get("ar") or 16 / 9)
    w = min(1.0, (9 / 16 if mode == "crop" else 16 / 9) / ar) / zoom
    h = 1.0 / zoom
    px = 0.5 if w >= 0.999 else max(0.0, min(1.0, (face["x"] - w / 2) / (1 - w)))
    py = 0.5 if h >= 0.999 else max(0.0, min(1.0, (face["y"] - h / 2) / (1 - h)))
    return {"px": round(px, 3), "py": round(py, 3)}


# ---- readable score: 0-100 from parts you can see ---------------------------

SCORE_WEIGHTS = {"hook": 25, "story": 25, "energy": 20, "clarity": 15, "pacing": 15}


def score_breakdown(c: "Clip", segments: list[dict], loud: list[float] | None,
                    energy_weight: float = 1.0) -> dict:
    """Each part 0-1. hook/story come from the verify model (falling back to the picker's
    score); energy, clarity and pacing are measured, so they still separate clips when the
    model rates everything alike."""
    s1 = max(0.0, min(1.0, c.score / 10))
    arc = c.arc or {}
    rated = bool(arc)                       # set by verify (zeros included); {} = no usable verdict
    hook = arc.get("hook", 0) / 3 if rated else s1
    story = (arc.get("arc", 0) / 3 * 0.6 + (arc.get("standalone", 0) + arc.get("quote", 0)) / 4 * 0.4) if rated else s1
    if c._rank_pct is not None:
        # the forced ranking is the model's most reliable signal (comparing beats rating);
        # it also breaks the ties absolute scores leave
        hook = 0.6 * hook + 0.4 * c._rank_pct
        story = 0.6 * story + 0.4 * c._rank_pct

    energy = 0.5
    stats = _loud_stats(loud or [])
    en = _clip_energy(c, loud) if loud else {}
    if stats and en:
        span = max(stats["p92"] - stats["p40"], 3.0)
        energy = max(0.0, min(1.0, (en["payoff"] - stats["p40"]) / span))

    hits = [s for s in segments if s["start"] < c.end_seconds and s["end"] > c.start_seconds]
    length = max(c.end_seconds - c.start_seconds, 0.1)
    talk = sum(min(s["end"], c.end_seconds) - max(s["start"], c.start_seconds) for s in hits)
    conf = [(s.get("conf"), s["end"] - s["start"]) for s in hits if s.get("conf") is not None]
    clarity = 0.7                                             # older transcripts carry no confidence
    if conf:
        lp = sum(v * d for v, d in conf) / max(sum(d for _, d in conf), 0.1)
        clarity = max(0.0, min(1.0, (lp + 1.0) / 0.8))        # -1.0 mumbled … -0.2 crisp
    wps = sum(len(s["text"].split()) for s in hits) / length
    rate = 1.0 if 2.0 <= wps <= 3.6 else max(0.0, min(1.0, wps / 2.0)) if wps < 2.0 else max(0.5, 1 - (wps - 3.6) / 3)
    pacing = 0.6 * rate + 0.4 * max(0.0, min(1.0, talk / length))

    parts = {"hook": hook, "story": story, "energy": energy, "clarity": clarity, "pacing": pacing}
    # the category's energy_weight rescales how much loudness counts (a podcast's best line
    # is often quiet); the weights are renormalised so the total stays out of 100
    w = dict(SCORE_WEIGHTS, energy=SCORE_WEIGHTS["energy"] * max(0.0, energy_weight))
    total = sum(w[k] * parts[k] for k in parts) * 100 / sum(w.values())
    return {"total": round(total), **{k: round(v, 2) for k, v in parts.items()}}


# ---- category: one cheap call classifying the whole video's content genre ---

CATEGORY_FILE = HERE / "categories.toml"


@functools.lru_cache(maxsize=1)
def _profiles() -> dict[str, dict]:
    """categories.toml's [category.<name>] tables; {} when the file is missing or broken
    (then everything behaves as before, from clipfinder.toml alone)."""
    try:
        import tomllib
        raw = tomllib.loads(CATEGORY_FILE.read_text(encoding="utf-8"))
        return {str(k).lower(): v for k, v in (raw.get("category") or {}).items() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001
        return {}


def category_profile(category: str) -> dict:
    p = _profiles()
    return {**p.get("general", {}), **p.get((category or "").lower(), {})}


CATEGORIES = tuple(k for k in _profiles() if k != "general") or (
    "gaming", "podcast", "vlog", "interview", "tutorial", "comedy", "sports", "reaction", "food")


def apply_profile(cfg: dict) -> dict:
    """Fold the chosen category's profile into cfg (length window, loudness weight, hot
    threshold, default taste). The user's own options always win over the default taste."""
    prof = category_profile(cfg.get("category") or "general")
    if not prof:
        return {}
    for key in ("min_seconds", "max_seconds"):
        if isinstance(prof.get(key), (int, float)):
            cfg[key] = float(prof[key])
    cfg["energy_weight"] = float(prof.get("energy_weight", 1.0))
    cfg["hot_threshold"] = float(prof.get("hot_threshold", 0.45))
    if not cfg.get("taste_from_user") and prof.get("default_taste"):
        cfg["taste"] = prof["default_taste"]
    cfg["profile"] = prof
    return prof


CATEGORIZE_BRIEF = (
    "Classify a video's content category from a short transcript excerpt. Reply with ONLY "
    f"a JSON object of this exact shape and nothing else: "
    '{"category":"...","confidence":0.0}. `category` must be exactly one of: '
    f"{', '.join(CATEGORIES)}. `confidence` is 0.0-1.0."
)


def detect_category(segments: list[dict], cfg: dict) -> str:
    """One lightweight Groq call, run once per video on a short excerpt before Stage 1
    picking, steering both picker stages' prompts (_steer_line) and _auto_edit's heuristics.
    Purely additive: any failure falls back to "general" and never blocks analysis."""
    if not segments:
        return "general"
    try:
        import groq
        # start, middle and end: the opening alone is often an intro or a trip somewhere
        # (a food challenge read as "vlog" from its first 40 lines)
        n = len(segments)
        picks = segments[:15] + segments[max(0, n // 2 - 7):n // 2 + 8] + segments[max(0, n - 12):]
        seen, sample = set(), []
        for s in picks:
            if id(s) not in seen:
                seen.add(id(s))
                sample.append(s)
        excerpt = as_timeline(sample)
        prompt = f"{CATEGORIZE_BRIEF}\n\nTRANSCRIPT EXCERPT:\n{excerpt}"
        resp = _groq_chat(groq.Groq(), cfg["groq_model"], prompt, 0.0)
        data = _loads_lenient(resp.choices[0].message.content)
        cat = str(data.get("category") or "").strip().lower()
        return cat if cat in CATEGORIES else "general"
    except Exception as e:  # noqa: BLE001 - category detection must never break analysis
        print(f"        category detection skipped: {e}")
        return "general"


def _steer_line(cfg: dict) -> str:
    """Combined video-category + editor-taste steering text injected into both the Stage 1
    picker prompts and the Stage 2 verify prompt. Either half is optional."""
    parts = []
    if cfg.get("category") and cfg["category"] != "general":
        parts.append(f"Video category: {cfg['category']}.")
    prof = cfg.get("profile") or {}
    if prof.get("moment"):
        parts.append(f"In this kind of video a great clip is {prof['moment']}.")
    if prof.get("prefer_beats"):
        parts.append(f"Favour these beat types: {', '.join(prof['prefer_beats'])}.")
    if cfg.get("taste"):
        parts.append(f"Editor's taste to follow: {cfg['taste']}")
    return f"\n{' '.join(parts)}\n" if parts else ""


# ---- picker: Groq (free) ----------------------------------------------------

WINDOW_MIN_FRAC = 0.6        # a window may end anywhere from 60% to 100% of `span`
WINDOW_OVERLAP = 20.0        # seconds of the previous window repeated at the next one's start
_LEX_SIDE = 45.0             # seconds of words compared on each side of a candidate cut
_STOP = set("the and that this with have just like what about there they them then were your "
            "you're it's i'm gonna going know really yeah okay right well some from would could "
            "been when here because think".split())


def _content_words(segs: list[dict]) -> set[str]:
    return {w for s in segs for w in re.findall(r"[a-z']{4,}", s["text"].lower()) if w not in _STOP}


def _cut_score(segments: list[dict], k: int) -> float:
    """How good a place is "between segments[k-1] and segments[k]" to start a new window:
    a longer pause and a change of vocabulary (TextTiling-style: the words of the 45 s
    before vs after) both say the topic moved on."""
    gap = max(0.0, segments[k]["start"] - segments[k - 1]["end"])
    t = segments[k]["start"]
    before = _content_words([s for s in segments[:k] if s["end"] > t - _LEX_SIDE])
    after = _content_words([s for s in segments[k:] if s["start"] < t + _LEX_SIDE])
    shift = 1.0 - (len(before & after) / len(before | after)) if (before or after) else 0.0
    return min(gap, 6.0) / 6.0 + shift


def _windows(segments: list[dict], span: float) -> list[list[dict]]:
    """Transcript windows for the picker, cut where the topic changes instead of every
    `span` seconds. Once a window is WINDOW_MIN_FRAC of `span` long, it ends at the best
    cut point (_cut_score) before `span`; the next window re-includes the last
    WINDOW_OVERLAP seconds so a moment right at a cut is seen whole at least once
    (duplicate picks are removed later by _nonoverlap)."""
    if not segments:
        return []
    out, i, n = [], 0, len(segments)
    while i < n:
        base = segments[i]["start"]
        j = i
        while j < n and segments[j]["start"] - base <= span:
            j += 1
        if j >= n:                                        # the rest fits: last window
            out.append(segments[i:])
            break
        lo = next((k for k in range(i + 1, j) if segments[k]["start"] - base >= WINDOW_MIN_FRAC * span), j)
        cut = max(range(lo, j + 1), key=lambda k: _cut_score(segments, k)) if lo < j else j
        out.append(segments[i:cut])
        back = segments[cut]["start"] - WINDOW_OVERLAP
        nxt = next((k for k in range(i + 1, cut) if segments[k]["start"] >= back), cut)
        i = max(nxt, i + 1)                               # always make progress
    return out


def _loads_lenient(raw: str) -> dict:
    raw = (raw or "").strip()
    candidates = [raw]
    candidates += re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    m = re.search(r'\{[^{]*"clips".*\}', raw, re.DOTALL) or re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            d = json.loads(c)
            if isinstance(d, dict):
                return d
        except (json.JSONDecodeError, TypeError):
            continue
    return {}


# Optional hook set by the multi-user worker (app/groq_gate.py) to serialise concurrent
# analyses against the shared free-tier TPM budget. Groq limits each model separately, so
# the gate keeps one budget per model. Signature: (est_tokens: int, model: str) -> None.
GROQ_GATE = None

# A per-minute limit clears within ~60s. A retry-after much longer than that is the daily
# cap (TPD / audio-seconds-per-day): sleeping it out held the job "analyzing" for hours.
GROQ_MAX_RETRY_WAIT = 120


class GroqQuotaError(RuntimeError):
    """Groq asked us to wait longer than GROQ_MAX_RETRY_WAIT — its daily limit is spent."""

    def __init__(self, model: str, wait: int):
        mins = max(1, round(wait / 60))
        super().__init__(f"Groq's free-tier daily limit for {model} is used up — "
                         f"try again in about {mins} min")
        self.model, self.wait = model, wait


def _retry_wait(e, model: str) -> int:
    """Seconds to back off for a 429/413, or raise GroqQuotaError if it's the daily cap."""
    wait = 62
    try:
        wait = int(float(e.response.headers.get("retry-after", "0"))) or wait
    except Exception:
        pass
    if wait > GROQ_MAX_RETRY_WAIT:
        raise GroqQuotaError(model, wait) from e
    return wait


def _groq_chat(client, model: str, prompt: str, temperature: float, effort: str = "low"):
    import groq

    if GROQ_GATE is not None:
        try:
            GROQ_GATE(len(prompt) // 4 + GROQ_PICK_MAX_TOKENS, model)
        except Exception:  # noqa: BLE001
            pass

    # gpt-oss is a reasoning model: at the default effort its hidden thinking ate the whole
    # 5000-token budget, the JSON got cut off (finish_reason "length") and every analysis
    # paid for a retry. Picking and verifying need little deliberation, so keep it low.
    extra = {"reasoning_effort": effort} if "gpt-oss" in model else {}
    for _ in range(4):
        try:
            return client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}],
                temperature=temperature, max_tokens=GROQ_PICK_MAX_TOKENS, **extra,
            )
        except groq.BadRequestError:
            raise
        except (groq.RateLimitError, groq.APIStatusError) as e:
            if getattr(e, "status_code", None) not in (429, 413):
                raise
            wait = _retry_wait(e, model)
            print(f"        rate limited (free tier) - waiting {wait}s ...", flush=True)
            time.sleep(wait)
    raise RuntimeError("Groq rate limit did not clear after several waits")


def _groq_pick_window(client, model: str, w: list[dict], ask: int, taste_line: str,
                      temperature: float) -> list[Clip]:
    text = as_timeline(w)
    if len(text) > 9000 and len(w) > 8:
        mid = len(w) // 2
        return (_groq_pick_window(client, model, w[:mid], max(2, ask // 2 + 1), taste_line, temperature)
                + _groq_pick_window(client, model, w[mid:], max(2, ask // 2 + 1), taste_line, temperature))

    # placeholders, not numbers: with 0.0 / 0.0 in the template the model sometimes handed
    # the template straight back - a single 0-second "clip", and 0 clips for the video
    shape = ('{"clips":[{"start_seconds":<s>,"end_seconds":<s>,"title":"<2-5 words>","hook":"<verbatim>",'
             '"why":"<one line>","quote":"<verbatim>","score":<1-10>}]}')
    prompt = (
        f"{PICKER_BRIEF}\n\n"
        f"Below is one section of a video's timestamped transcript. End your reply with a "
        f"JSON object of this exact shape and nothing after it:\n{shape}\n\n"
        f"Give the {ask} best standalone clips in this section - never an empty list: return at "
        f"least 2 candidates even if the section is quiet; weak ones are filtered out afterwards. Rules:\n"
        f"- start_seconds: the transcript timestamp of the SETUP line (the build-up), not the punchline.\n"
        f"- end_seconds: AFTER the payoff / punchline / final reaction - never end on the setup. "
        f"length 15-45s, ideally 20-35s: a clip under 15s is almost always missing its setup or "
        f"its reaction, so widen it to the whole beat rather than return a fragment.\n"
        f"- title: 2-5 punchy words naming the moment, like a Shorts title (never empty).\n"
        f"- hook: the exact first words of the clip, copied from the transcript (not a description).\n"
        f"- quote: the single funniest line, verbatim from the transcript.\n"
        f"- score: an integer from 1 to 10 (NOT 0-100).\n"
        f"{taste_line}\n"
        f"TRANSCRIPT SECTION:\n{text}"
    )
    for attempt in range(2):
        if attempt == 0:
            resp = _groq_chat(client, model, prompt, temperature)
        else:
            # at low effort the model sometimes just answers {"clips":[]} for a short section
            # that has moments in it; insist. (Medium effort was tried here: its thinking ran
            # past the 5000-token reply cap, and raising the cap risks the free-tier TPM.)
            nudge = ("\n\nIMPORTANT: an empty list is not an answer. This section has moments "
                     "worth reviewing - return at least 2 candidates; weak ones are filtered "
                     "out afterwards.")
            resp = _groq_chat(client, model, prompt + nudge, temperature + 0.15)
        msg = resp.choices[0]
        data = _loads_lenient(msg.message.content)
        clips, bad = [], 0
        for item in data.get("clips", []):
            try:
                c = Clip.model_validate(item)
            except ValidationError:
                bad += 1
                continue
            if c.end_seconds - c.start_seconds < 3.0:     # an echoed template, not a pick
                bad += 1
                continue
            clips.append(c)
        if clips:
            return clips
        if msg.finish_reason == "length":
            print("        (window hit the token limit - retrying)")
        elif data.get("clips"):
            print(f"        ({bad} clips failed validation - retrying)")
        elif "clips" in data:
            print("        (model returned no clips - retrying)")
        else:
            print("        (no JSON in reply - retrying)")
    return []


def _find_clips_groq(segments: list[dict], cfg: dict, loud: list[float] | None = None):
    import groq

    client = groq.Groq()
    model = cfg["groq_model"]
    n = cfg["count"]
    wins = _windows(segments, PICKER_WINDOW_SEC) or [segments]
    taste_line = _steer_line(cfg)
    share = max(2, round(n / len(wins)))
    # ask for a deeper bench than we keep: with low reasoning effort the replies are cheap,
    # and the final 0-100 ranking can only choose well if there are spare candidates
    ask, keep = share + 4, share + 3

    pool: list[Clip] = []
    for wi, w in enumerate(wins, 1):
        if len(wins) > 1:
            print(f"        window {wi}/{len(wins)} ...", flush=True)
        clips = _groq_pick_window(client, model, w, ask, taste_line, cfg["temperature"])
        clips.sort(key=lambda c: -c.score)
        pool.extend(_nonoverlap(clips, keep))

    if not pool:
        # every window came back without usable JSON (a model hiccup, seen once) - fail
        # loudly so the editor offers Retry, rather than a silent "no clips found"
        raise RuntimeError("The clip picker returned no usable answer. Retry the analysis.")
    return _finalize(pool, segments, n, cfg, loud), None


# ---- picker: Claude (better) -----------------------------------------------

def _pick_max_tokens(cfg: dict) -> int:
    return min(12000, 2000 + int(cfg["count"] or 0) * 400)


def _find_clips_claude(segments: list[dict], cfg: dict, loud: list[float] | None = None):
    import anthropic

    client = anthropic.Anthropic()
    dur = segments[-1]["end"] if segments else 0.0
    taste_line = _steer_line(cfg)
    user = (
        f"Full timestamped transcript of a {dur / 60:.0f}-minute video is below.\n\n"
        f"Pick the {cfg['count']} best standalone clips. Copy exact second values from the "
        f"transcript into start_seconds / end_seconds: start on the SETUP line, end AFTER the "
        f"payoff / punchline (never on the setup). length 10-45s. `hook` = the exact opening "
        f"words verbatim, `quote` = the funniest line verbatim.{taste_line}\n"
        f"TRANSCRIPT:\n{as_timeline(segments)}"
    )
    resp = client.messages.parse(
        model=cfg["claude_model"], max_tokens=_pick_max_tokens(cfg), system=PICKER_BRIEF,
        messages=[{"role": "user", "content": user}], output_format=ClipSet,
    )
    return _finalize(list(resp.parsed_output.clips), segments, cfg["count"], cfg, loud), resp.usage


def find_clips(segments: list[dict], cfg: dict, loud: list[float] | None = None):
    if cfg["backend"] == "claude":
        model = cfg["claude_model"]
        in_tok = _rough_tokens(PICKER_BRIEF) + _rough_tokens(as_timeline(segments)) + 400
        if _affordable(cfg, model, in_tok, _pick_max_tokens(cfg), "clip pick"):
            clips, usage = _find_clips_claude(segments, cfg, loud)
            _budget_charge(cfg, model, usage, "pick")
            return clips, usage
        print("        clip pick: using Groq (free) to stay in budget", flush=True)
    return _find_clips_groq(segments, cfg, loud)


# ---- stage 2: verify the arc, refine the bounds, score each beat -------------


def _norm_line(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def _snap_to_lines(c: Clip, segments: list[dict]) -> None:
    """Pull an edge to the exact segment carrying the model's hook_line / payoff_line.
    Purely additive - silently does nothing when there's no match."""
    h = _norm_line(c.hook_line)
    if len(h) > 8:
        for s in segments:
            st = _norm_line(s["text"])
            if st and (h in st or st in h) and abs(s["start"] - c.start_seconds) < 10:
                c.start_seconds = s["start"]
                break
    p = _norm_line(c.payoff_line)
    if len(p) > 8:
        for s in reversed(segments):
            st = _norm_line(s["text"])
            if st and (p in st or st in p) and abs(s["end"] - c.end_seconds) < 10:
                c.end_seconds = s["end"]
                break


def _ctx_lines(c: Clip, segments: list[dict], pre: float = 14.0, post: float = 9.0) -> str:
    a, b = c.start_seconds - pre, c.end_seconds + post
    rows = []
    for s in segments:
        if s["end"] <= a or s["start"] >= b:
            continue
        inside = s["start"] >= c.start_seconds - 0.3 and s["end"] <= c.end_seconds + 0.3
        mm, ss = divmod(int(s["start"]), 60)
        rows.append(f"  [{'IN ' if inside else 'ctx'} {mm:02d}:{ss:02d}|{s['start']:.1f}s] {s['text']}")
    if len(rows) > 16:                                # keep the prompt small for the 8k TPM tier
        rows = rows[:9] + ["  [.. middle of the clip trimmed ..]"] + rows[-6:]
    return "\n".join(rows)


class _Verdict(BaseModel):
    index: int
    keep: bool = True
    start_seconds: float | None = None
    end_seconds: float | None = None
    hook_line: str = ""
    payoff_line: str = ""
    beat_type: str = "moment"
    hook: int = 0
    arc: int = 0
    quote: int = 0
    standalone: int = 0
    trend: int = 0
    rank: int = 0                          # 1 = best clip in this batch; forced, no ties


class _VerifySet(BaseModel):
    verdicts: list[_Verdict]


_VERIFY_BRIEF = (
    "You are refining a shortlist of short-form video clips. In each CLIP, lines tagged [IN ..] are "
    "inside the clip; lines tagged [ctx ..] are a few seconds of lead-in / tail for context ONLY.\n"
    "For EVERY clip do ALL THREE:\n"
    "1) SCORE it against these anchors. Use the whole range - most batches have at least one 0 "
    "or 1 and at most one top score per criterion; identical scores across clips are wrong.\n"
    "   hook 0-3: 0 = opens mid-thought / filler, 1 = understandable but flat, 2 = a clear "
    "question, claim or tension in the first line, 3 = you cannot scroll past it.\n"
    "   arc 0-3: 0 = no payoff inside the IN lines, 1 = payoff implied or cut off, 2 = setup and "
    "payoff both present, 3 = tight setup -> escalation -> payoff/reaction.\n"
    "   quote 0-2: 0 = nothing repeatable, 1 = a decent line, 2 = a line people would comment back.\n"
    "   standalone 0-2: 0 = needs earlier context, 1 = mostly clear, 2 = zero context needed.\n"
    "   trend 0-2: 0 = slow or talky, 1 = fine, 2 = quick punchline, loop/rewatch potential.\n"
    "2) RANK it - `rank` orders ALL clips from best (1) to worst (N) as a short-form editor "
    "would post them. Every clip gets a different rank; no ties.\n"
    "3) TIGHTEN it - start_seconds = the first line a viewer needs (use a [ctx] line when the setup "
    "is there), end_seconds = just after the payoff / final reaction. Use the exact Ns timestamps "
    "shown. Copy hook_line and payoff_line verbatim. Label beat_type: reaction | fail | bit | story "
    "| quote | wholesome | moment.\n"
    "Set keep=false ONLY when the clip is genuinely broken - garbled speech-to-text word-salad, OR "
    "it ends before any payoff, OR it staples two unrelated moments together - and give a one-line "
    "`reason`. If you cannot name a concrete flaw, keep=true. Most clips are keep=true.\n\n"
    # The worked example shows only how to TIGHTEN and copy lines. It used to carry scores
    # (2/3/2/2/2) and both gpt-oss models echoed them onto every clip - the "identical
    # verdicts" that flattened the ranking. Scores come from the anchors above, not an example.
    "EXAMPLE (bounds and lines only)\n"
    "### CLIP 9\n"
    "  [ctx 01:10|70.0s] anyway so my chair broke yesterday\n"
    "  [IN  01:14|74.0s] and I'm sitting here streaming right\n"
    "  [IN  01:16|76.0s] and it just SNAPS, I hit the floor\n"
    "  [IN  01:18|78.0s] chat is DYING I can't breathe\n"
    "  [ctx 01:22|82.0s] okay let me get up\n"
    '-> start_seconds 70.0 (the setup is in the ctx line), end_seconds 81.0, '
    'hook_line "anyway so my chair broke yesterday", payoff_line "chat is DYING I can\'t breathe", '
    'beat_type "fail"'
)

_VERIFY_SHAPE = ('{"verdicts":[{"index":<int>,"keep":<bool>,"start_seconds":<s>,"end_seconds":<s>,'
                 '"hook_line":"","payoff_line":"","beat_type":"",'
                 '"hook":<0-3>,"arc":<0-3>,"quote":<0-2>,"standalone":<0-2>,"trend":<0-2>,'
                 '"rank":<1-N>,"reason":""}]}')


def _rank_key(c: Clip, segments: list[dict], loud: list[float] | None, energy_weight: float = 1.0) -> float:
    """What the final shortlist is ordered by: the same 0-100 the editor shows, so the clips
    you get are the top-scored ones (ties broken by the blended model score)."""
    return score_breakdown(c, segments, loud, energy_weight)["total"] + c.score / 100


def _verify_and_refine(clips: list[Clip], segments: list[dict], cfg: dict,
                       loud: list[float] | None = None) -> list[Clip]:
    """Stage 2 - one LLM pass over every candidate: confirm it is a complete beat, tighten the
    bounds, label the beat type, assign a decomposed score. REFINES and re-ranks; only a clip the
    model explicitly rejects is a hard drop, and never below MIN_CLIPS."""
    if not clips:
        return clips
    want = max(int(cfg.get("min_clips", MIN_CLIPS)), int(cfg.get("count") or 0))
    ew = float(cfg.get("energy_weight", 1.0))

    floor = float(cfg.get("quality_floor", 40))

    def shortlist(pool: list[Clip]) -> list[Clip]:
        """Top `want` by the 0-100 score, no overlaps, in time order, none below the quality
        floor (but never empty). Every exit goes through this - the early "keep stage-1
        picks" exits used to hand back the whole pool, so asking for 5 clips could return 8."""
        r = {id(c): _rank_key(c, segments, loud, ew) for c in pool}
        ranked = sorted(pool, key=lambda x: -r[id(x)])
        top = _nonoverlap([c for c in ranked if r[id(c)] >= floor], want) or _nonoverlap(ranked, 1)
        return sorted(top, key=lambda x: x.start_seconds)

    body = _VERIFY_BRIEF + _steer_line(cfg) + "\n\n" + "\n\n".join(
        f"### CLIP {i}\n{_ctx_lines(c, segments)}" for i, c in enumerate(clips))
    verdicts: dict[int, dict] = {}
    vmodel = _verify_model(cfg)
    use_claude = cfg["backend"] == "claude" and _affordable(
        cfg, vmodel, _rough_tokens(body) + 400, 4000, "flow check")
    if cfg["backend"] == "claude" and not use_claude:
        print("        flow check: using Groq (free) to stay in budget", flush=True)
    try:
        if use_claude:
            import anthropic
            r = anthropic.Anthropic().messages.parse(
                model=vmodel, max_tokens=4000,
                messages=[{"role": "user", "content": body}],
                output_format=_VerifySet)
            _budget_charge(cfg, vmodel, r.usage, "verify")
            for v in r.parsed_output.verdicts:
                verdicts[v.index] = v.model_dump()
        else:
            import groq
            client, prompt = groq.Groq(), (
                body + f"\n\nReply with ONLY this JSON object and nothing after it:\n{_VERIFY_SHAPE}")
            judge = cfg.get("groq_verify_model") or cfg["groq_model"]
            for attempt in range(2):
                try:
                    resp = _groq_chat(client, judge, prompt, 0.0)
                except GroqQuotaError:
                    # the judge model's daily cap is separate from the picker's: a weaker
                    # judge still beats skipping the flow check entirely
                    if judge == cfg["groq_model"]:
                        raise
                    judge = cfg["groq_model"]
                    print(f"        flow check: judge quota spent, using {judge}", flush=True)
                    resp = _groq_chat(client, judge, prompt, 0.0)
                for v in _loads_lenient(resp.choices[0].message.content).get("verdicts", []):
                    try:
                        verdicts[int(v["index"])] = v
                    except (KeyError, ValueError, TypeError):
                        pass
                if verdicts:
                    break
    except Exception as e:  # noqa: BLE001 - never let the flow check break analysis
        print(f"        flow check skipped: {e}")
        return shortlist(clips)
    if not verdicts:
        print("        flow check: no usable verdicts - keeping stage-1 picks")
        return shortlist(clips)

    def ci(x, hi):
        return max(0, min(hi, int(x or 0)))

    def _sum(v):
        return sum(ci(v.get(k), 3 if k in ("hook", "arc") else 2)
                   for k in ("hook", "arc", "quote", "standalone", "trend"))
    # a weak free model sometimes rejects (or zero-scores) everything - then its keep flags are
    # noise; fall back to trusting stage 1, only using whatever bounds/scores it did give.
    any_signal = any(_sum(v) > 0 or (v.get("reason") or "").strip() for v in verdicts.values())
    trust_drops = any_signal and not all(v.get("keep") is False for v in verdicts.values())
    if not any_signal:
        print("        flow check: model gave no signal - keeping stage-1 picks")
        return shortlist(clips)

    # A small model often hands every clip the same breakdown (seen: 1/2/1/1/1 on all six).
    # Then its numbers rank nothing - keep the stage-1 score, which carries the energy boost.
    tuples = [tuple(ci(v.get(k), 3 if k in ("hook", "arc") else 2)
                    for k in ("hook", "arc", "quote", "standalone", "trend"))
              for v in verdicts.values() if _sum(v) > 0]
    flat = len(tuples) >= 3 and len(set(tuples)) == 1
    if flat:
        print("        flow check: identical scores for every clip - ranking by stage 1 instead")

    # forced rank -> 1.0 (best) … 0.0 (worst); only trusted when it really orders the batch
    ranks = {i: int(v.get("rank") or 0) for i, v in verdicts.items() if str(v.get("rank") or "").strip().lstrip("-").isdigit()}
    good_ranks = sorted(r for r in ranks.values() if r > 0)
    rank_ok = len(good_ranks) >= 3 and len(set(good_ranks)) == len(good_ranks)
    lo_r, hi_r = (good_ranks[0], good_ranks[-1]) if rank_ok else (0, 0)

    dur = segments[-1]["end"] if segments else 0.0
    firm: list[Clip] = []          # model kept it AND the arc reads complete
    weak: list[Clip] = []          # model rejected it OR arc looked thin - fallback pool
    rejected_ids: set[int] = set()
    for i, c in enumerate(clips):
        v = verdicts.get(i)
        if v is None:
            firm.append(c)                                    # model skipped this one - trust stage 1
            continue
        h, a, q, sd = ci(v.get("hook"), 3), ci(v.get("arc"), 3), ci(v.get("quote"), 2), ci(v.get("standalone"), 2)
        t = ci(v.get("trend"), 2)
        # any_signal holds here, so even an all-zero verdict is a real judgement ("opens
        # mid-thought, no payoff") - it used to read as "unrated" and fall back to stage 1,
        # which scored a clip the judge rated 0/0/0/0/0 at 61/100
        scored = not flat
        # refine bounds when the model gave sane ones
        s2, e2 = v.get("start_seconds"), v.get("end_seconds")
        try:
            s2, e2 = float(s2), float(e2)
            # never let verify tighten below the minimum: the clamp would pad the start back
            # out with lead-in, and the beat's payoff was usually what got trimmed
            if c.start_seconds - 20 <= s2 < e2 <= c.end_seconds + 15 and (e2 - s2) >= cfg["min_seconds"]:
                c.start_seconds, c.end_seconds = s2, e2
                _snap_to_lines(c, segments)
                c.start_seconds = max(0.0, c.start_seconds - cfg["lead_seconds"])
                c.end_seconds = min(dur or c.end_seconds, c.end_seconds + cfg["tail_seconds"])
            else:
                _snap_to_lines(c, segments)
        except (TypeError, ValueError):
            _snap_to_lines(c, segments)
        if rank_ok and ranks.get(i, 0) > 0:
            c._rank_pct = round(1 - (ranks[i] - lo_r) / max(hi_r - lo_r, 1), 3)
        c.beat_type = str(v.get("beat_type") or "moment")
        c.hook_line = str(v.get("hook_line") or "")
        c.payoff_line = str(v.get("payoff_line") or "")
        # flat verdicts carry no information - leave arc empty so the readable score's
        # hook/story parts fall back to the stage-1 score instead of the same 1/2/1/1/1
        c.arc = {} if flat else {"hook": h, "arc": a, "quote": q, "standalone": sd, "trend": t}
        if scored:
            # blend, don't replace: stage 1 (1-10) already holds the loudness boost; stage 2
            # (0-12, rescaled to 10) adds the structure read. Replacing threw the energy away.
            c.score = round(0.5 * c.score + 0.5 * (h + a + q + sd + t) * 10 / 12, 2)
        # verify often tightens below min_seconds; let the punchline play on before the clamp
        # pads the *start* backwards (that produced 10.0 s clips that were mostly lead-in)
        _grow_to_payoff(c, segments, cfg)
        _clamp_clip(c, dur, cfg, segments)
        _end_on_sentence(c, segments, cfg, dur)
        rejected = trust_drops and v.get("keep") is False
        if rejected:
            rejected_ids.add(id(c))
        (weak if (rejected or (scored and a < 2)) else firm).append(c)

    # `want` is a maximum. Fill it with clips that clear the quality floor; a thin-but-decent
    # clip may top up the list, one the judge rejected never does. Fewer strong clips beat
    # padding to the number (the review screen says "3 strong moments - you asked for 5").
    rank = {id(c): _rank_key(c, segments, loud, ew) for c in firm + weak}
    by_rank = lambda pool: sorted(pool, key=lambda x: -rank[id(x)])  # noqa: E731
    kept = [c for c in by_rank(firm) if rank[id(c)] >= floor]
    if len(kept) < want:
        kept += [c for c in by_rank(weak)
                 if id(c) not in rejected_ids and rank[id(c)] >= floor][:want - len(kept)]
    kept = _nonoverlap(by_rank(kept), want)
    kept.sort(key=lambda x: x.start_seconds)
    return kept or shortlist(clips)   # nothing cleared the floor: still hand back the best one


# ------------------------------------------------------------------ optional vision pass (Claude)

class VisualNote(BaseModel):
    index: int
    visual_score: int = Field(ge=1, le=5)
    note: str


class VisualSet(BaseModel):
    notes: list[VisualNote]


def vision_check(video: Path, clips: list[Clip], workdir: Path, model: str,
                 cfg: dict | None = None) -> dict[int, VisualNote]:
    import anthropic

    content: list[dict] = []
    for i, c in enumerate(clips):
        frame = workdir / f"frame_{i:02d}.jpg"
        grab_frame(video, (c.start_seconds + c.end_seconds) / 2, frame)
        b64 = base64.standard_b64encode(frame.read_bytes()).decode("ascii")
        content.append({"type": "text", "text": f"Clip {i}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text":
                    "For each clip index give visual_score 1-5 (5 = face/reaction clearly on "
                    "screen and lively; 1 = static screen, menu, chat, or black) and a short note."})

    model = model if model.startswith("claude") else DEFAULTS["claude_model"]
    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=model, max_tokens=4000,
        messages=[{"role": "user", "content": content}], output_format=VisualSet,
    )
    if cfg is not None:
        _budget_charge(cfg, model, getattr(resp, "usage", None), "vision")
    return {note.index: note for note in resp.parsed_output.notes}


# ------------------------------------------------------------------ per-video driver

def slugify(text: str, limit: int = 48) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", text)[:limit] or "clip"


def process_video(video: Path, cfg: dict) -> dict:
    print(f"\n=== {video.name} ===", flush=True)
    budget_reset(cfg)
    out_dir = (Path(cfg["out"]) if cfg["out"] else video.parent / "clips") / video.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="clipfinder_") as tmp:
        workdir = Path(tmp)

        print("  [1/4] extracting audio ...", flush=True)
        mp3 = extract_audio(video, workdir)

        print("  [2/4] transcribing (Groq) ...", flush=True)
        t0 = time.time()
        segments = transcribe(mp3, workdir, cfg)
        if not segments:
            print("  no speech found - skipping.")
            return {"video": video.name, "clips": []}
        print(f"        {len(segments)} segments, {time.time() - t0:.0f}s", flush=True)

        label = cfg["claude_model"] if cfg["backend"] == "claude" else cfg["groq_model"]
        print(f"  [3/4] picking {cfg['count']} clips ({cfg['backend']}: {label}) ...", flush=True)
        clips, usage = find_clips(segments, cfg)
        if cfg.get("coherence_check", True) and clips:
            before = len(clips)
            clips = _verify_and_refine(clips, segments, cfg)
            if len(clips) < before:
                print(f"        flow check tightened / dropped {before - len(clips)} clip(s)", flush=True)
        if usage is not None:
            ci, co = COST_PER_MTOK.get(cfg["claude_model"], (0, 0))
            cost = usage.input_tokens / 1e6 * ci + usage.output_tokens / 1e6 * co
            print(f"        {len(clips)} clips | {usage.input_tokens} in / "
                  f"{usage.output_tokens} out tok | ~${cost:.3f}", flush=True)
        else:
            print(f"        {len(clips)} clips | Groq (free)", flush=True)

        visuals: dict[int, VisualNote] = {}
        vmodel = _vision_model(cfg)
        if cfg["vision"] and clips and _affordable(
                cfg, vmodel, len(clips) * 1700 + 300, len(clips) * 140, "vision"):
            print(f"  [3b] vision check ({vmodel}) ...", flush=True)
            try:
                visuals = vision_check(video, clips, workdir, vmodel, cfg)
            except Exception as e:
                print(f"        vision check skipped: {e}")

        sp = cfg.get("_spend") or {}
        if sp.get("events"):
            models = ", ".join(sorted({e["model"] for e in sp["events"]}))
            cap = f" / ${sp['cap']:.2f} cap" if sp.get("cap") else ""
            print(f"        Claude spend: ~${sp['usd']:.3f}{cap}  ({models})", flush=True)

        records = []
        for i, c in enumerate(clips):
            rec = c.model_dump()
            rec["duration"] = round(c.end_seconds - c.start_seconds, 1)
            if i in visuals:
                rec["visual_score"] = visuals[i].visual_score
                rec["visual_note"] = visuals[i].note
            records.append(rec)

        print("  picks:")
        for i, r in enumerate(records):
            vs = f" v{r['visual_score']}" if "visual_score" in r else ""
            print(f"   {i+1:2d}. [{r['start_seconds']:7.1f}-{r['end_seconds']:7.1f}s "
                  f"{r['duration']:4.1f}s] s{r['score']:.0f}{vs}  {r['title']}")
            print(f"       hook: {r['hook']}")

        (out_dir / "clips.json").write_text(
            json.dumps({"video": str(video), "backend": cfg["backend"], "model": label,
                        "transcript_segments": len(segments), "clips": records},
                       indent=2, ensure_ascii=False), encoding="utf-8")

        if cfg["dry_run"]:
            print("  --dry-run: not cutting.")
            return {"video": video.name, "clips": records}

        print(f"  [4/4] cutting {len(clips)} clips -> {out_dir}", flush=True)
        for i, c in enumerate(clips):
            cut_clip(video, c.start_seconds, c.end_seconds,
                     out_dir / f"{i+1:02d}_{slugify(c.title)}.mp4", cfg["vertical"],
                     loudnorm=cfg.get("loudnorm", True))
        print(f"  done: {out_dir}")

    return {"video": video.name, "clips": records}


# ------------------------------------------------------------------ cli

def main() -> int:
    load_dotenv(HERE / ".env")

    ap = argparse.ArgumentParser(description="Find short-form clips in long videos.")
    ap.add_argument("path", help="a video file, or a folder of videos")
    ap.add_argument("--config", default=None, help="path to a clipfinder.toml (default: next to script)")
    ap.add_argument("--n", type=int, default=None, help="clips per video")
    ap.add_argument("--taste", default=None, help="free-text editing preferences")
    ap.add_argument("--picker", choices=["groq", "claude"], default=None)
    ap.add_argument("--model", default=None, help="LLM model id for the chosen picker")
    ap.add_argument("--verify-model", default=None, help="Claude model for the stage-2 flow check (default: same as --model)")
    ap.add_argument("--vision-model", default=None, help="Claude model for the vision pass (default: same as --model)")
    ap.add_argument("--claude-budget", type=float, default=None,
                    help="USD cap on Claude spend per video; over it, stages fall back to Groq (0 = no cap)")
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--lead", type=float, default=None, help="seconds of pad before each clip")
    ap.add_argument("--tail", type=float, default=None, help="seconds of pad after each clip")
    ap.add_argument("--vertical", action="store_true", default=None, help="also crop to 1080x1920")
    ap.add_argument("--no-loudnorm", dest="loudnorm", action="store_false", default=None,
                    help="skip -14 LUFS loudness normalization")
    ap.add_argument("--vision", action="store_true", default=None, help="Claude scores one frame per clip")
    ap.add_argument("--dry-run", action="store_true", help="transcript + picks, no cutting")
    ap.add_argument("--out", default="", help="output root (default: <video dir>/clips)")
    args = ap.parse_args()

    cfg = load_config(Path(args.config) if args.config else HERE / "clipfinder.toml")
    for key, val in {"count": args.n, "taste": args.taste, "backend": args.picker,
                     "temperature": args.temperature, "lead_seconds": args.lead,
                     "tail_seconds": args.tail, "vertical": args.vertical,
                     "loudnorm": args.loudnorm, "vision": args.vision,
                     "verify_model": args.verify_model, "vision_model": args.vision_model,
                     "claude_budget": args.claude_budget}.items():
        if val is not None:
            cfg[key] = val
    if args.model:
        cfg["claude_model" if cfg["backend"] == "claude" else "groq_model"] = args.model
    cfg["dry_run"] = args.dry_run
    cfg["out"] = args.out

    if not os.environ.get("GROQ_API_KEY"):
        print(f"missing GROQ_API_KEY - add it to {HERE / '.env'}  (free at console.groq.com)")
        return 2
    if (cfg["backend"] == "claude" or cfg["vision"]) and not os.environ.get("ANTHROPIC_API_KEY"):
        what = "--picker claude" if cfg["backend"] == "claude" else "--vision"
        print(f"{what} needs ANTHROPIC_API_KEY in {HERE / '.env'} (console.anthropic.com), or drop it")
        return 2
    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found on PATH.")
        return 2

    target = Path(args.path)
    if target.is_dir():
        videos = sorted(p for p in target.rglob("*") if p.suffix.lower() in VIDEO_EXTS)
        if not videos:
            print(f"no videos in {target}")
            return 2
        print(f"{len(videos)} videos in {target}")
    elif target.is_file():
        videos = [target]
    else:
        print(f"not found: {target}")
        return 2

    summary = []
    for v in videos:
        try:
            summary.append(process_video(v, cfg))
        except Exception as e:
            print(f"  ERROR on {v.name}: {e}")
            summary.append({"video": v.name, "error": str(e)})

    total = sum(len(s.get("clips", [])) for s in summary)
    print(f"\n{'='*50}\n{total} clips from {len(videos)} video(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             