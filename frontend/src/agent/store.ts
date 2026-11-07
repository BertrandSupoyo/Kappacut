import { create } from "zustand";
import { useEditor, type AgentOp } from "@/editor/store";

export type Proposal =
  | { type: "clips"; clips: number[] }
  | { type: "choices"; options: string[] }
  | { type: "render" };

export interface AgentReply {
  reply: string;
  ops: AgentOp[];
  changes: { clip: number; title: string; items: string[] }[];
  proposals: Proposal[];
}

export interface ChatTurn {
  id: number;
  role: "user" | "agent";
  text: string;
  file?: { name: string; size: number };
  changes?: AgentReply["changes"];
  /** how to put back what this turn changed */
  undo?: AgentOp[];
  undone?: boolean;
  proposals?: Proposal[];
  /** the person has answered this turn's cards and buttons, so they no longer apply */
  answered?: boolean;
  /** the request failed: an error message, not part of the conversation */
  failed?: boolean;
  /** analysis stopped: offer to run it again */
  retry?: boolean;
  /** clips have rendered: link to them */
  rendered?: boolean;
}

interface AgentChat {
  turns: ChatTurn[];
  projectId: string | null;
  /** a project whose analysis is still to come, and what the person asked when they sent it */
  awaiting: { projectId: string; ask: string | null } | null;
  /** typed into the main page's box, to send when this page opens */
  queuedAsk: string | null;
  /** what a sign-out interrupted, to put back in the box on the way back */
  draft: string | null;
  /** projects where the person has answered "which clips?" */
  decided: Record<string, boolean>;
  /** the preview beside the chat: the clips with their edits, or the rendered files */
  previewView: "clip" | "files";
  previewHidden: boolean;

  add: (turn: Omit<ChatTurn, "id">) => void;
  update: (id: number, patch: Partial<ChatTurn>) => void;
  /** Moves to a project. Older turns' cards and Undo were about the last one, so they stop working:
   * left active, they'd change the new project's clips by number. */
  setProject: (id: string | null) => void;
  setAwaiting: (a: AgentChat["awaiting"]) => void;
  queueAsk: (text: string) => void;
  takeQueuedAsk: () => string | null;
  setDraft: (text: string) => void;
  takeDraft: () => string | null;
  markDecided: (id: string, value: boolean) => void;
  setPreviewView: (view: AgentChat["previewView"]) => void;
  setPreviewHidden: (hidden: boolean) => void;
  reset: () => void;
}

let nextId = 0;

/** Only in this tab: nothing typed here is stored, so a reload starts a new chat. */
export const useAgentChat = create<AgentChat>((set, get) => ({
  turns: [],
  projectId: null,
  awaiting: null,
  queuedAsk: null,
  draft: null,
  decided: {},
  previewView: "clip",
  previewHidden: false,
  add: (turn) => set({ turns: [...get().turns, { ...turn, id: ++nextId }] }),
  update: (id, patch) => set({ turns: get().turns.map((t) => (t.id === id ? { ...t, ...patch } : t)) }),
  setProject: (projectId) => {
    if (projectId === get().projectId) return;
    set({ projectId, previewView: "clip", turns: get().turns.map((t) => ({ ...t, answered: true, undo: undefined })) });
  },
  setAwaiting: (awaiting) => set({ awaiting }),
  queueAsk: (queuedAsk) => set({ queuedAsk }),
  takeQueuedAsk: () => {
    const text = get().queuedAsk;
    if (text) set({ queuedAsk: null });
    return text;
  },
  setDraft: (draft) => set({ draft }),
  takeDraft: () => {
    const text = get().draft;
    if (text) set({ draft: null });
    return text;
  },
  markDecided: (id, value) => set({ decided: { ...get().decided, [id]: value } }),
  setPreviewView: (previewView) => set({ previewView }),
  setPreviewHidden: (previewHidden) => set({ previewHidden }),
  reset: () => set({ turns: [], projectId: null, awaiting: null, previewView: "clip", previewHidden: false }),
}));

/** Puts a clip in the preview. `reveal` is for when the person asked to see it: the chat moving on by
 * itself shouldn't reopen a preview they've hidden, or scroll the page out from under them. */
export function showClip(clip: number, reveal = false) {
  useEditor.getState().setActive(clip);
  useAgentChat.setState(reveal ? { previewView: "clip", previewHidden: false } : { previewView: "clip" });
  if (reveal) document.querySelector(".agent-preview")?.scrollIntoView({ block: "nearest" });
}

/** What the model is reminded of. An undo is part of the conversation: without it, "do the same for
 * clip 3" could mean a change the person threw away. A message that failed isn't: left in, it reads as
 * a question still waiting, so "hi" got an answer to it. */
export function historyFrom(turns: ChatTurn[]) {
  return turns
    .filter((t, i) => !t.failed && t.text && !(t.role === "user" && turns[i + 1]?.failed))
    .slice(-10)
    .map((t) => ({
      role: t.role === "agent" ? ("assistant" as const) : ("user" as const),
      text: (t.undone ? `${t.text} (The person then undid these changes.)` : t.text).slice(0, 1900),
    }));
}
