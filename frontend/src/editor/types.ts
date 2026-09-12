import type { Clip } from "@/api/types";

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
