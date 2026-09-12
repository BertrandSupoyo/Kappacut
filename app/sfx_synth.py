"""Synthesise the builtin sound effects onto the media volume (no auth/web imports —
safe to call from the worker)."""
from __future__ import annotations

from app import storage

_SYNTH = {
    "whoosh": ["-f", "lavfi", "-i", "anoisesrc=d=0.55:c=pink:a=0.6",
               "-af", "highpass=f=250,lowpass=f=5500,afade=t=in:d=0.28:curve=ipar,"
                      "afade=t=out:st=0.28:d=0.27,volume=1.6"],
    "impact": ["-f", "lavfi", "-i", "sine=frequency=90:duration=0.35",
               "-af", "afade=t=out:d=0.34,volume=2.2,alimiter=limit=0.9"],
    "pop":    ["-f", "lavfi", "-i", "sine=frequency=520:duration=0.12",
               "-af", "afade=t=out:st=0.02:d=0.1,volume=0.8"],
    "ding":   ["-f", "lavfi", "-i", "sine=frequency=1240:duration=0.6",
               "-af", "afade=t=out:d=0.58,volume=0.55"],
    "riser":  ["-f", "lavfi", "-i", "anoisesrc=d=1.2:c=white:a=0.35",
               "-af", "highpass=f=400,afade=t=in:d=1.1,volume=1.4"],
}

BUILTIN_NAMES = tuple(_SYNTH)


def synth_builtins() -> None:
    import clipfinder as cf

    d = storage.builtin_sfx_dir()
    for name, args in _SYNTH.items():
        f = d / f"{name}.m4a"
        if not f.exists():
            try:
                cf.run([cf.FFMPEG, "-y", *args, "-c:a", "aac", "-b:a", "128k", str(f)])
            except Exception:  # noqa: BLE001 — a missing builtin is not fatal
                pass
