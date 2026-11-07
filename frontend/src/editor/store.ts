import { create } from "zustand";
import { jpost } from "@/api/client";
import type { Clip, Segment } from "@/api/types";
import { applyAuto, defaultEdit, type ClipEdit, type PlacedSfx } from "./types";

export interface RenderProgress {
  status: "queued" | "running" | "done" | "error" | null;
  done: number;
  total: number;
  current: string;
  error: string | null;
}

interface SavedState {
  v: 2;
  activeIdx: number;
  edits: Record<number, ClipEdit>;
}

/** One field of one clip's edit, as the AI editor changed it. */
export interface AgentOp {
  clip: number;
  /** e.g. "caption.style" */
  path: string;
  value: unknown;
}

export interface AgentTurn {
  id: number;
  role: "user" | "assistant";
  text: string;
  changes?: { clip: number; title: string; items: string[] }[];
  /** how to put back what this answer changed */
  undo?: AgentOp[];
  undone?: boolean;
  /** the request failed, so this is an error message and not part of the conversation */
  failed?: boolean;
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
  /** One render job per editor, shared by every Render button and the outputs list. */
  rendering: boolean;
  renderProgress: RenderProgress | null;
  setRender: (p: { rendering?: boolean; renderProgress?: RenderProgress | null }) => void;

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
  setAllKeep: (keep: boolean) => void;
  /** Set the active clip's start/end at `t`, keeping the 1s minimum the trim handles use. */
  markIn: (t: number) => void;
  markOut: (t: number) => void;
  toggleAuto: (i: number) => void;
  addSfx: (i: number, sfx: PlacedSfx) => void;
  removeSfx: (i: number, at: number, file: string) => void;
  keptRanges: () => Array<Record<string, unknown>>;

  agentChat: AgentTurn[];
  addAgentTurn: (turn: Omit<AgentTurn, "id">) => void;
  clearAgentChat: () => void;
  /** Applies them as one change and returns what would put them back. Field by field, so anything
   * else changed on the clip while the AI was working stays as it is. */
  applyAgentOps: (ops: AgentOp[]) => AgentOp[];
  undoAgentTurn: (id: number) => void;
}

let agentTurnId = 0;

let saveTimer: ReturnType<typeof setTimeout> | null = null;
let pendingSave: (() => void) | null = null;

function scheduleSave(get: () => EditorState, set: (p: Partial<EditorState>) => void) {
  const { projectId } = get();
  if (!projectId) return;
  set({ saveStatus: "saving" });
  if (saveTimer) clearTimeout(saveTimer);
  const save = () => {
    saveTimer = null;
    pendingSave = null;
    // The edits belong to `projectId`. The timer used to read whatever project was loaded
    // when it fired, so opening another project within the delay saved that one's edits
    // over this one's; init() and the editor's unmount flush before the store moves on.
    if (get().projectId !== projectId) return;
    const { activeIdx, edits } = get();
    jpost(`/api/projects/${projectId}/edit`, { state: { v: 2, activeIdx, edits } satisfies SavedState })
      .then(() => get().projectId === projectId && set({ saveStatus: "saved" }))
      .catch(() => get().projectId === projectId && set({ saveStatus: "error" }));
  };
  pendingSave = save;
  saveTimer = setTimeout(save, 900);
}

/** Send a debounced save now, while the store still holds that project's edits. */
export function flushSave() {
  if (saveTimer) clearTimeout(saveTimer);
  pendingSave?.();
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
  rendering: false,
  renderProgress: null,
  setRender: (p) => set(p),
  agentChat: [],

  init: ({ projectId, clips, segments, duration, saved }) => {
    flushSave();
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
      // a render belongs to the project it was started on
      ...(projectId !== get().projectId ? { rendering: false, renderProgress: null, agentChat: [] } : {}),
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

  setAllKeep: (keep) => {
    const { clips, edits } = get();
    const next = { ...edits };
    clips.forEach((c, i) => {
      next[i] = { ...(edits[i] ?? defaultEdit(c)), keep };
    });
    set({ edits: next });
    scheduleSave(get, set);
  },

  markIn: (t) => get().patch(get().activeIdx, (e) => { e.start = Math.max(0, Math.min(t, e.end - 1)); }),
  markOut: (t) => get().patch(get().activeIdx, (e) => { e.end = Math.max(t, e.start + 1); }),

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
            ? {
                mode: e.look.mode, px: e.look.px, py: e.look.py, zoom: e.look.zoom, blur: e.look.blur,
                ...(e.look.follow && c.face?.track ? { track: c.face.track } : {}),
              }
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

  addAgentTurn: (turn) => set({ agentChat: [...get().agentChat, { ...turn, id: ++agentTurnId }] }),
  clearAgentChat: () => set({ agentChat: [] }),

  applyAgentOps: (ops) => {
    const { clips, edits } = get();
    const next = { ...edits };
    const undo: AgentOp[] = [];
    for (const { clip, path, value } of ops) {
      if (!clips[clip]) continue;                 // re-analyzed since the question was asked
      const edit = structuredClone(next[clip] ?? defaultEdit(clips[clip]));
      const keys = path.split(".");
      const last = keys.pop() as string;
      let target = edit as unknown as Record<string, unknown>;
      for (const key of keys) target = target[key] as Record<string, unknown>;
      undo.unshift({ clip, path, value: target[last] });
      target[last] = value;
      next[clip] = edit;
    }
    set({ edits: next });
    scheduleSave(get, set);
    return undo;
  },

  undoAgentTurn: (id) => {
    const turn = get().agentChat.find((t) => t.id === id);
    if (!turn?.undo || turn.undone) return;
    get().applyAgentOps(turn.undo);
    set({ agentChat: get().agentChat.map((t) => (t.id === id ? { ...t, undone: true } : t)) });
  },
}));
