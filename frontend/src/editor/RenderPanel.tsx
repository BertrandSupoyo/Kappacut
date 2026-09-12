import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { jget, jpost } from "@/api/client";
import type { RenderOutput } from "@/api/types";
import { useToast } from "@/components/Toast";
import { useSSE } from "@/lib/sse";
import { fmtDuration } from "@/lib/format";
import { useEditor } from "./store";

interface RenderSSE {
  status: "queued" | "running" | "done" | "error" | null;
  done: number;
  total: number;
  current: string;
  error: string | null;
}

export function RenderPanel() {
  const projectId = useEditor((s) => s.projectId);
  const keptRanges = useEditor((s) => s.keptRanges);
  const saveStatus = useEditor((s) => s.saveStatus);
  const editsVersion = useEditor((s) => s.edits);
  const toast = useToast();
  const qc = useQueryClient();
  const [rendering, setRendering] = useState(false);

  const ranges = keptRanges();
  const sse = useSSE<RenderSSE>(`/api/projects/${projectId}/render/events`, rendering);

  const { data: outputs } = useQuery({
    queryKey: ["outputs", projectId],
    queryFn: () => jget<{ clips: RenderOutput[] }>(`/api/projects/${projectId}/outputs`),
    refetchInterval: rendering ? 2000 : false,
  });

  useEffect(() => {
    if (sse?.status === "done" || sse?.status === "error") {
      setRendering(false);
      qc.invalidateQueries({ queryKey: ["outputs", projectId] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      if (sse.status === "error") toast(sse.error || "Render failed.", "error");
    }
  }, [sse?.status, projectId, qc, toast, sse?.error]);

  async function render() {
    setRendering(true);
    try {
      await jpost(`/api/projects/${projectId}/render`, { ranges });
    } catch (e) {
      setRendering(false);
      toast(String((e as { message?: string })?.message || "Couldn't start the render."), "error");
    }
  }

  const saveLabel =
    saveStatus === "saving" ? "Saving…" : saveStatus === "error" ? "Save failed" : saveStatus === "saved" ? "Saved" : "";

  return (
    <div className="glass" style={{ padding: "var(--sp-4)", position: "sticky", top: "var(--sp-4)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "var(--sp-3)" }}>
        <h3 style={{ fontSize: "var(--fs-md)" }}>Render</h3>
        <span className="muted" style={{ fontSize: "var(--fs-2xs)" }}>{saveLabel}</span>
      </div>

      <button className="btn" style={{ width: "100%" }} disabled={rendering || ranges.length === 0} onClick={render}>
        {rendering
          ? sse?.status === "running"
            ? `${sse.current || "Rendering"} (${sse.done}/${sse.total})`
            : "Starting…"
          : `Render ${ranges.length} clip${ranges.length === 1 ? "" : "s"}`}
      </button>

      {(outputs?.clips.length ?? 0) > 0 && (
        <>
          <h3 style={{ fontSize: "var(--fs-sm)", margin: "var(--sp-4) 0 var(--sp-2)" }}>Rendered</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
            {outputs!.clips.map((o) => (
              <div key={o.file} className="glass" style={{ padding: 8, fontSize: "var(--fs-xs)" }}>
                <video src={o.url} controls preload="metadata" style={{ width: "100%", borderRadius: "var(--r-xs)", maxHeight: o.vertical ? 260 : undefined }} />
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
                  <span className="muted" style={{ wordBreak: "break-all" }}>{o.file}</span>
                  <a href={o.url} download style={{ whiteSpace: "nowrap" }}>{fmtDuration(o.duration)} ↓</a>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      {/* editsVersion referenced so the panel re-derives ranges on any edit */}
      <span hidden>{Object.keys(editsVersion).length}</span>
    </div>
  );
}
