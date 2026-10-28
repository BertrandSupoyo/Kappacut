import type { Clip, ClipFace } from "@/api/types";

/** The face position at source time `t` along a camera path (linear between keyframes). */
export function faceAt(face: ClipFace, t: number): ClipFace {
  const tr = face.track;
  if (!tr || tr.length < 2) return face;
  if (t <= tr[0][0]) return { ...face, x: tr[0][1], y: tr[0][2] };
  for (let k = 1; k < tr.length; k++) {
    if (t < tr[k][0]) {
      const [t0, x0, y0] = tr[k - 1];
      const [t1, x1, y1] = tr[k];
      const f = (t - t0) / Math.max(t1 - t0, 1e-3);
      return { ...face, x: x0 + (x1 - x0) * f, y: y0 + (y1 - y0) * f };
    }
  }
  const last = tr[tr.length - 1];
  return { ...face, x: last[1], y: last[2] };
}

/** px/py that centre the render's crop window on a face — mirrors kappacut.frame_on. */
export function faceFrame(face: ClipFace | undefined, mode: CropMode, zoom: number): { px: number; py: number } {
  if (!face) return { px: 0.5, py: 0.5 };
  const ar = face.ar || 16 / 9;
  const w = Math.min(1, (mode === "crop" ? 9 / 16 : 16 / 9) / ar) / zoom;
  const h = 1 / zoom;
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  return {
    px: w >= 0.999 ? 0.5 : +clamp((face.x - w / 2) / (1 - w)).toFixed(3),
    py: h >= 0.999 ? 0.5 : +clamp((face.y - h / 2) / (1 - h)).toFixed(3),
  };
}

export type CropMode = "crop" | "fill" | "cropfill";

export interface PlacedSfx {
  file: string;
  name: string;
  at: number; // clip-local seconds
  gain: number;
}

export interface ClipEdit {
  keep: boolean;
  start: number;
  end: number;
  caption: {
    on: boolean;
    style: "clean" | "bold" | "box" | "pop";
    position: "bottom" | "center" | "top";
    size: number;
    anim: "none" | "pop" | "fade" | "slide" | "bounce";
    karaoke: boolean;
  };
  look: {
    vertical: boolean;
    mode: CropMode;
    px: number;
    py: number;
    zoom: number;
    blur: number;
    /** pan with the detected face over time instead of sitting at px/py */
    follow?: boolean;
  };
  color: { preset: string; b: number; c: number; s: number };
  sfx: PlacedSfx[];
  sfxAuto: boolean;
  autoOff: boolean;
}

export const CAPTION_STYLES = ["clean", "bold", "box", "pop"] as const;
export const CAPTION_ANIMS = ["none", "pop", "fade", "slide", "bounce"] as const;
export const CAPTION_POSITIONS = ["bottom", "center", "top"] as const;
export const CROP_MODES: { id: CropMode; label: string }[] = [
  { id: "crop", label: "Crop" },
  { id: "fill", label: "Blur fill" },
  { id: "cropfill", label: "Subject + blur" },
];
export const COLOR_PRESETS = ["none", "punchy", "warm", "cool", "film", "mono"] as const;

export function defaultEdit(c: Clip): ClipEdit {
  return {
    keep: true,
    start: c.start_seconds,
    end: c.end_seconds,
    caption: { on: true, style: "clean", position: "bottom", size: 1, anim: "none", karaoke: false },
    look: { vertical: false, mode: "crop", px: 0.5, py: 0.42, zoom: 1, blur: 24 },
    color: { preset: "none", b: 0, c: 1, s: 1 },
    sfx: [],
    sfxAuto: false,
    autoOff: false,
  };
}

/** Seed a ClipEdit from the analysis's per-clip `auto` suggestion. */
export function applyAuto(e: ClipEdit, auto: Record<string, unknown> | undefined): ClipEdit {
  if (!auto) return e;
  const a = auto as {
    vertical?: boolean;
    crop?: Partial<ClipEdit["look"]>;
    caption?: { style?: ClipEdit["caption"]["style"]; anim?: ClipEdit["caption"]["anim"]; karaoke?: boolean };
    color?: ClipEdit["color"];
    sfx?: { file: string; at?: number; gain?: number }[];
  };
  const next: ClipEdit = structuredClone(e);
  if (a.vertical) next.look.vertical = true;
  if (a.crop) Object.assign(next.look, a.crop);
  if (a.caption) {
    next.caption.style = a.caption.style ?? next.caption.style;
    next.caption.anim = a.caption.anim ?? next.caption.anim;
    next.caption.karaoke = !!a.caption.karaoke;
  }
  if (a.color) next.color = { ...next.color, ...a.color };
  if (Array.isArray(a.sfx) && next.sfx.length === 0) {
    next.sfx = a.sfx.map((s) => ({
      file: s.file,
      name: s.file.replace(/\.\w+$/, ""),
      at: Number(s.at) || 0,
      gain: s.gain ?? -4,
    }));
  }
  return next;
}
