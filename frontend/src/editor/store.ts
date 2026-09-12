import { create } from "zustand";
import { jpost } from "@/api/client";
import type { Clip, Segment } from "@/api/types";
import { applyAuto, defaultEdit, type ClipEdit, type PlacedSfx } from "./types";

interface SavedState {
  v: 2;
  activeIdx: number;
  edits: Record<number, ClipEdit>;
}

interface EditorState {
  projectId: string;
  clips: Clip[];
  segments: Segment[];
  duration: number;
  snaps: number[];
  edits: Record<number, ClipEdit>;
  activeIdx: number;
  playhead: number;
  autoStyle: boolean;
  saveStatus: "idle" | "saving" | "saved" | "error";

  init: (args: {
    projectId: string;
    clips: Clip[];
    segments: Segment[];
    duration: number;
    saved: SavedState | null;
  }) => void;
  setActive: (i: number) => void;
  setPlayhead: (t: number) => void;
  edit: (i: number) => ClipEdit;
  patch: (i: number, fn: (e: ClipEdit) => void) => void;
  toggleKeep: (i: number) => void;
  toggleAuto: (i: number) => void;
  addSfx: (i: number, sfx: PlacedSfx) => void;
  removeSfx: (i: number, at: number, file: string) => void;
  keptRanges: () => Array<Record<string, unknown>>;
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleSave(get: () => EditorState, set: (p: Partial<EditorState>) => void) {
  const { projectId } = get();
  if (!projectId) return;
  set({ saveStatus: "saving" });
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    const { activeIdx, edits } = get();
    try {
      await jpost(`/api/projects/${projectId}/edit`, { state: { v: 2, activeIdx, edits } satisfies SavedState });
      set({ saveStatus: "saved" });
    } catch {
      set({ saveStatus: "error" });
    }
  }, 900);
}

export const useEditor = create<EditorState>((set, get) => ({
  projectId: "",
  clips: [],
  segments: [],
  duration: 0,
  snaps: [],
  edits: {},
  activeIdx: 0,
  playhead: 0,
  autoStyle: true,
  saveStatus: "idle",

  init: ({ projectId, clips, segments, duration, saved }) => {
    const snaps = [...new Set(segments.flatMap((s) => [+s.start, +s.end]))]
      .filter((t) => t >= 0)
      .sort((a, b) => a - b);
    const edits: Record<number, ClipEdit> = {};
    clips.forEach((c, i) => {
      const base = saved?.edits?.[i] ?? defaultEdit(c);
      // seed the AI auto-edit once, unless the user turned it off
      edits[i] = saved?.edits?.[i] ? base : applyAuto(base, c.auto);
    });
    set({
      projectId,
      clips,
      segments,
      duration,
      snaps,
      edits,
      activeIdx: Math.min(saved?.activeIdx ?? 0, Math.max(0, clips.length - 1)),
      playhead: clips[saved?.activeIdx ?? 0]?.start_seconds ?? 0,
      saveStatus: saved ? "saved" : "idle",
    });
  },

  setActive: (i) => set({ activeIdx: i, playhead: get().clips[i]?.start_seconds ?? 0 }),
  setPlayhead: (t) => set({ playhead: t }),

  edit: (i) => get().edits[i] ?? defaultEdit(get().clips[i]),

  patch: (i, fn) => {
    const cur = get().edits[i] ?? defaultEdit(get().clips[i]);
    const next = structuredClone(cur);
    fn(next);
    set({ edits: { ...get().edits, [i]: next } });
    scheduleSave(get, set);
  },

  toggleKeep: (i) => get().patch(i, (e) => { e.keep = !e.keep; }),

  toggleAuto: (i) => {
    const c = get().clips[i];
    const cur = get().edits[i] ?? defaultEdit(c);
    let next: ClipEdit;
    if (cur.autoOff) {
      next = applyAuto(defaultEdit(c), c.auto);
      next.autoOff = false;
      next.keep = cur.keep;
    } else {
      next = defaultEdit(c);
      next.autoOff = true;
      next.keep = cur.keep;
    }
    set({ edits: { ...get().edits, [i]: next } });
    scheduleSave(get, set);
  },

  addSfx: (i, sfx) => get().patch(i, (e) => { e.sfx = [...e.sfx.filter((s) => !(s.file === sfx.file && Math.abs(s.at - sfx.at) < 0.05)), sfx]; }),
  removeSfx: (i, at, file) => get().patch(i, (e) => { e.sfx = e.sfx.filter((s) => !(s.file === file && s.at === at)); }),

  keptRanges: () => {
    const { clips, edits } = get();
    return clips
      .map((c, i) => ({ c, e: edits[i] ?? defaultEdit(c), i }))
      .filter(({ e }) => e.keep)
      .map(({ c, e }) => {
        const colorSet = e.color.preset !== "none" || e.color.b !== 0 || e.color.c !== 1 || e.color.s !== 1;
        return {
          start: e.start,
          end: e.end,
          vertical: e.look.vertical,
          label: c.title || c.hook || "clip",
          crop: e.look.vertical
            ? { mode: e.look.mode, px: e.look.px, py: e.look.py, zoom: e.look.zoom, blur: e.look.blur }
            : null,
          captions: e.caption.on
            ? {
                style: e.caption.style,
                position: e.caption.position,
                size: e.caption.size,
                anim: e.caption.anim,
                karaoke: e.caption.karaoke,
              }
            : null,
          color: colorSet ? e.color : null,
          sfx: [
            ...e.sfx.map((s) => ({ file: s.file, at: s.at, gain: s.gain })),
            ...(e.sfxAuto ? [{ file: "whoosh.m4a", at: 0, gain: -3 }] : []),
          ],
        };
      });
  },
}));
