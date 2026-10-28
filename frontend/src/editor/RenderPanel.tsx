import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { jget, jpost } from "@/api/client";
import type { RenderOutput } from "@/api/types";
import { useToast } from "@/components/Toast";
import { useSSE } from "@/lib/sse";
import { fmtDuration } from "@/lib/format";
import { useEditor, type RenderProgress } from "./store";

/** The kept ranges, re-derived whenever an edit changes. */
export function useKeptRanges() {
  const edits = useEditor((s) => s.edits);
  const clips = useEditor((s) => s.clips);
  const keptRanges = useEditor((s) => s.keptRanges);
  return useMemo(() => keptRanges(), [edits, clips, keptRanges]);
}

/** Mounted once per editor: owns the render SSE so every Render button shares one job. */
export function RenderSync() {
  const projectId = useEditor((s) => s.projectId);
  const rendering = useEditor((s) => s.rendering);
  const setRender = useEditor((s) => s.setRender);
  const qc = useQueryClient();
  const toast = useToast();
  const sse = useSSE<RenderProgress>(projectId ? `/api/projects/${projectId}/render/events` : null, rendering);

  useEffect(() => {
    if (!rendering || !sse) return;
    setRender({ renderProgress: sse });
    if (sse.status === "done" || sse.status === "error") {
      setRender({ rendering: false });
      qc.invalidateQueries({ queryKey: ["outputs", projectId] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      if (sse.status === "error") toast(sse.error || "Render failed.", "error");
      // clips render independently now: the job finishes even when a few of them failed
      else if (sse.error) toast(sse.error, "error");
    }
  }, [sse, rendering, projectId, qc, toast, setRender]);

  return null;
}

export function RenderButton({ block = false }: { block?: boolean }) {
  const projectId = useEditor((s) => s.projectId);
  const rendering = useEditor((s) => s.rendering);
  const progress = useEditor((s) => s.renderProgress);
  const setRender = useEditor((s) => s.setRender);
  const ranges = useKeptRanges();
  const toast = useToast();
  const [starting, setStarting] = useState(false);

  async function render() {
    setStarting(true);
    try {
      await jpost(`/api/projects/${projectId}/render`, { ranges });
      // only listen once the new job exists — /render/events reports the *latest* render
      // job, so subscribing before the POST lands can read the previous job's "done"
      setRender({ rendering: true, renderProgress: null });
    } catch (e) {
      toast(String((e as { message?: string })?.message || "Couldn't start the render."), "error");
    } finally {
      setStarting(false);
    }
  }

  const label = starting
    ? "Starting…"
    : rendering
      ? progress?.status === "running"
        ? `Rendering ${progress.done}/${progress.total}`
        : "Starting…"
      : `Render ${ranges.length} clip${ranges.length === 1 ? "" : "s"}`;

  return (
    <button
      className="btn"
      style={block ? { width: "100%" } : undefined}
      disabled={starting || rendering || ranges.length === 0 || !projectId}
      onClick={render}
      title={ranges.length === 0 ? "Keep at least one clip to render" : undefined}
    >
      {label}
    </button>
  );
}

export function SaveStatus() {
  const saveStatus = useEditor((s) => s.saveStatus);
  if (saveStatus === "idle") return null;
  const text = saveStatus === "saving" ? "Saving…" : saveStatus === "error" ? "Save failed" : "Saved";
  return (
    <span
      className="muted"
      role="status"
      style={{ fontSize: "var(--fs-xs)", whiteSpace: "nowrap", color: saveStatus === "error" ? "var(--danger)" : undefined }}
    >
      {saveStatus === "saved" && <span aria-hidden style={{ color: "var(--good)" }}>✓ </span>}
      {text}
    </span>
  );
}

/** Rendered files for the project; renders nothing until there is at least one. */
export function RenderOutputs() {
  const projectId = useEditor((s) => s.projectId);
  const rendering = useEditor((s) => s.rendering);
  const { data: outputs } = useQuery({
    queryKey: ["outputs", projectId],
    queryFn: () => jget<{ clips: RenderOutput[] }>(`/api/projects/${projectId}/outputs`),
    // the store's projectId is "" until the editor inits — don't fetch /api/projects//outputs
    enabled: !!projectId,
    refetchInterval: rendering ? 2000 : false,
  });
  if (!outputs?.clips.length) return null;

  return (
    <section className="glass" style={{ padding: "var(--sp-4)" }} aria-label="Rendered clips">
      <h3 style={{ fontSize: "var(--fs-md)", marginBottom: "var(--sp-3)" }}>Rendered</h3>
      <div className="outputs-grid">
        {outputs.clips.map((o) => (
          <div key={o.file} className="glass" style={{ padding: 8, fontSize: "var(--fs-xs)", minWidth: 0 }}>
            <video src={o.url} controls preload="metadata" style={{ width: "100%", borderRadius: "var(--r-xs)", maxHeight: 280, background: "#000" }} />
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginTop: 6 }}>
              <span className="muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{o.file}</span>
              <a href={`${o.url}?download=1`} download style={{ whiteSpace: "nowrap" }}>{fmtDuration(o.duration)} ↓</a>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
