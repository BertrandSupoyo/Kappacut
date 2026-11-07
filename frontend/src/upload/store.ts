import { create } from "zustand";
import Uppy from "@uppy/core";
import Tus from "@uppy/tus";
import { ApiError, jdelete, jpost } from "@/api/client";
import { queryClient } from "@/lib/queryClient";
import { composeTaste, DEFAULT_SETTINGS, type ClipSettings } from "./settings";

// app/projects.py VIDEO_EXTS
const VIDEO_EXTENSIONS = ["mp4", "mkv", "webm", "mov", "avi", "m4v", "m2ts", "mts"];
export const VIDEO_ACCEPT = VIDEO_EXTENSIONS.map((e) => `.${e}`).join(",");

interface UploadState {
  phase: "idle" | "uploading" | "done";
  pct: number;
  fileName: string;
  fileBytes: number;
  projectId: string | null;
  /** the file on this computer, so it can be watched while it uploads */
  localUrl: string | null;
  error: string | null;
  settings: ClipSettings;
  setSettings: (patch: Partial<ClipSettings>) => void;
  resetSettings: () => void;
  /** Returns the new project's id, or null with `error` saying why. `ask` is what the person
   * typed alongside the video, which steers the clip picker. */
  start: (file: File, opts: { maxBytes: number; ask?: string }) => Promise<string | null>;
  cancel: () => void;
  /** Back to an empty box, ready for the next video. */
  dismiss: () => void;
}

let uppy: Uppy | null = null;

/** Lets go of the file on this computer. A page can't release this by itself, and it holds the file open. */
function noLocalFile(url: string | null) {
  if (url) URL.revokeObjectURL(url);
  return { localUrl: null };
}

/** Removes a project that never got its video. The lists refresh afterwards: any sooner, and they'd
 * fetch the project again and show a card that's already gone. */
function discard(projectId: string) {
  void jdelete(`/api/projects/${projectId}`)
    .catch(() => {})
    .finally(() => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["usage"] });
    });
}

function refusal(raw: unknown): string {
  const text = String(raw ?? "");
  if (/job running/i.test(text)) return "You already have a job running. Wait for it to finish, then try again.";
  if (/quota|limit/i.test(text)) return "This video is over one of your limits. See your limits on the Account page.";
  return "The upload didn't finish. Check your connection and try again.";
}

/** One upload at a time, kept here and not in a page, so it carries on when the person moves
 * between Projects and the AI agent, and both show the same progress. */
export const useUploads = create<UploadState>((set, get) => ({
  phase: "idle",
  pct: 0,
  fileName: "",
  fileBytes: 0,
  projectId: null,
  localUrl: null,
  error: null,
  settings: DEFAULT_SETTINGS,
  setSettings: (patch) => set({ settings: { ...get().settings, ...patch } }),
  resetSettings: () => set({ settings: DEFAULT_SETTINGS }),

  start: async (file, { maxBytes, ask }) => {
    if (get().phase === "uploading") {
      set({ error: "One video at a time. Wait for this upload to finish first." });
      return null;
    }
    const ext = (file.name.split(".").pop() ?? "").toLowerCase();
    if (!VIDEO_EXTENSIONS.includes(ext)) {
      set({ phase: "idle", error: `We can't use a .${ext} file. Try an MP4, MOV, MKV or WEBM.` });
      return null;
    }
    if (file.size > maxBytes) {
      set({ phase: "idle", error: `That video is over ${Math.round(maxBytes / 1024 ** 3)} GB. Try a shorter or smaller version.` });
      return null;
    }

    const s = get().settings;
    noLocalFile(get().localUrl);
    set({ phase: "uploading", pct: 0, fileName: file.name, fileBytes: file.size, projectId: null, localUrl: URL.createObjectURL(file), error: null });
    let projectId: string;
    try {
      const res = await jpost<{ id: string }>("/api/projects", {
        filename: file.name,
        taste: composeTaste(s, ask),
        auto_render: !s.review,
        clip_count: s.count ? Number(s.count) : null,
        content_type: s.type || null,
      });
      projectId = res.id;
    } catch (e) {
      const limit = e instanceof ApiError && e.status === 429;
      set({ phase: "idle", ...noLocalFile(get().localUrl), error: limit ? "You've reached your project limit. Delete a project to upload another." : "We couldn't start the upload. Please try again." });
      return null;
    }
    // cancelled while the project was being created
    if (get().phase !== "uploading") {
      discard(projectId);
      return null;
    }
    set({ projectId });

    const u = new Uppy({ autoProceed: true }).use(Tus, {
      endpoint: "/files/",
      chunkSize: 8 * 1024 * 1024,
      withCredentials: true,
      removeFingerprintOnSuccess: true,
    });
    uppy = u;
    u.on("upload-progress", (_f, prog) => {
      if (prog.bytesTotal) set({ pct: Math.round((prog.bytesUploaded / prog.bytesTotal) * 100) });
    });
    u.on("complete", (result) => {
      if (uppy !== u) return;
      uppy = null;
      u.destroy();
      if (result.failed?.length) {
        set({ phase: "idle", pct: 0, projectId: null, ...noLocalFile(get().localUrl), error: refusal(result.failed[0]?.error) });
        discard(projectId);
      } else {
        set({ phase: "done", pct: 100, ...noLocalFile(get().localUrl) });
      }
    });
    u.addFile({ name: file.name, type: file.type, data: file, meta: { projectId, filename: file.name } });
    return projectId;
  },

  cancel: () => {
    const { projectId } = get();
    const u = uppy;
    uppy = null;
    if (u) {
      u.cancelAll();
      u.destroy();
    }
    set({ phase: "idle", pct: 0, projectId: null, ...noLocalFile(get().localUrl), error: null });
    if (projectId) discard(projectId);
  },

  dismiss: () => set({ phase: "idle", pct: 0, projectId: null, fileName: "", fileBytes: 0, ...noLocalFile(get().localUrl), error: null, settings: DEFAULT_SETTINGS }),
}));
