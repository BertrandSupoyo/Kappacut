import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { jget } from "@/api/client";
import type { ProjectDetail, SfxItem } from "@/api/types";
import { useSSE } from "@/lib/sse";
import { fmtDuration } from "@/lib/format";
import { useEditor } from "@/editor/store";
import { Player } from "@/editor/Player";
import { Timeline } from "@/editor/Timeline";
import { ClipList } from "@/editor/ClipList";
import { ClipCard } from "@/editor/ClipCard";
import { Inspector } from "@/editor/Inspector";
import { RenderPanel } from "@/editor/RenderPanel";

type ViewMode = "review" | "edit";

export function Editor() {
  const { id = "" } = useParams();
  // land on the finished-clips overview first; per-clip fine-tuning is an opt-in "Edit"
  const [view, setView] = useState<ViewMode>("review");
  useEffect(() => setView("review"), [id]);
  const setActive = useEditor((s) => s.setActive);
  function editClip(idx: number) {
    setActive(idx);
    setView("edit");
  }

  const { data: project } = useQuery({
    queryKey: ["project", id],
    queryFn: () => jget<ProjectDetail>(`/api/projects/${id}`),
    refetchInterval: (q) =>
      q.state.data && ["queued", "analyzing", "uploading"].includes(q.state.data.status) ? 2500 : false,
  });
  const { data: edit } = useQuery({
    queryKey: ["edit", id],
    queryFn: () => jget<{ state: unknown }>(`/api/projects/${id}/edit`),
    enabled: project?.status === "ready",
  });
  const { data: sfxData } = useQuery({
    queryKey: ["sfx"],
    queryFn: () => jget<{ sfx: SfxItem[] }>("/api/sfx"),
    staleTime: 300_000,
  });

  const analyzing = project ? ["queued", "analyzing", "uploading"].includes(project.status) : false;
  const analyzeSSE = useSSE<{ pct: number; msg: string }>(`/api/projects/${id}/analyze/events`, analyzing);

  const initEditor = useEditor((s) => s.init);
  const ready = project?.status === "ready" && project.analysis && edit !== undefined;

  useEffect(() => {
    if (ready && project?.analysis) {
      initEditor({
        projectId: id,
        clips: project.analysis.clips,
        segments: project.analysis.segments,
        duration: project.duration,
        saved: (edit?.state as never) ?? null,
      });
    }
  }, [ready, id, project, edit, initEditor]);

  // keyboard
  const setPlayhead = useEditor((s) => s.setPlayhead);
  const playhead = useEditor((s) => s.playhead);
  const patch = useEditor((s) => s.patch);
  const activeIdx = useEditor((s) => s.activeIdx);
  useEffect(() => {
    if (!ready || view !== "edit") return;
    const onKey = (ev: KeyboardEvent) => {
      const t = ev.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA") return;
      const v = document.querySelector("video");
      if (ev.key === " ") {
        ev.preventDefault();
        if (v) v.paused ? void v.play() : v.pause();
      } else if (ev.key === "ArrowRight") {
        setPlayhead(playhead + (ev.shiftKey ? 5 : 1 / 30));
      } else if (ev.key === "ArrowLeft") {
        setPlayhead(Math.max(0, playhead - (ev.shiftKey ? 5 : 1 / 30)));
      } else if (ev.key === "i") {
        patch(activeIdx, (e) => { e.start = playhead; });
      } else if (ev.key === "o") {
        patch(activeIdx, (e) => { e.end = playhead; });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ready, view, playhead, activeIdx, setPlayhead, patch]);

  if (!project) {
    return <div style={{ display: "grid", placeItems: "center", minHeight: "40vh" }}><div className="spin" /></div>;
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)", margin: "var(--sp-4) 0 var(--sp-5)" }}>
        <Link to="/app" className="muted" style={{ fontSize: "var(--fs-sm)" }}>← Projects</Link>
        <h1 style={{ fontSize: "var(--fs-xl)" }}>{project.name}</h1>
        {project.duration > 0 && <span className="muted" style={{ fontSize: "var(--fs-sm)" }}>{fmtDuration(project.duration)}</span>}
      </div>

      {analyzing && (
        <div className="glass" style={{ padding: "var(--sp-5)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-sm)", marginBottom: 10 }}>
            <span>{analyzeSSE?.msg || "Getting started…"}</span>
            <span className="muted">{analyzeSSE?.pct ?? 0}%</span>
          </div>
          <div style={{ height: 5, background: "var(--sub-bg)", borderRadius: 99, overflow: "hidden" }}>
            <div style={{ width: `${analyzeSSE?.pct ?? 0}%`, height: "100%", background: "var(--accent)", transition: "width .3s" }} />
          </div>
        </div>
      )}

      {project.status === "failed" && (
        <div className="glass" style={{ padding: "var(--sp-5)", borderColor: "color-mix(in srgb, var(--danger) 40%, transparent)" }}>
          <b style={{ color: "var(--danger)" }}>Analysis failed.</b>
          <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>{project.error || "Try re-uploading the video."}</p>
        </div>
      )}

      {project.status === "ready" && project.analysis && project.analysis.clips.length === 0 && (
        <div className="glass" style={{ padding: "var(--sp-5)" }}>
          <p className="muted" style={{ margin: 0 }}>No standalone clips found in this one. Try a longer or livelier source.</p>
        </div>
      )}

      {ready && project.analysis && project.analysis.clips.length > 0 && view === "review" && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: "var(--sp-4)",
            }}
          >
            {project.analysis.clips.map((_, i) => (
              <ClipCard key={i} index={i} onEdit={editClip} />
            ))}
          </div>
          <div style={{ marginTop: "var(--sp-4)" }}>
            <RenderPanel />
          </div>
        </>
      )}

      {ready && project.analysis && project.analysis.clips.length > 0 && view === "edit" && (
        <>
          <button className="btn ghost" style={{ padding: "6px 12px", marginBottom: "var(--sp-3)" }} onClick={() => setView("review")}>
            ← Back to clips
          </button>
          <ClipList />
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 320px", gap: "var(--sp-5)", alignItems: "start", marginTop: "var(--sp-4)" }}>
            <div>
              <Player />
              <Timeline />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
              <Inspector sfx={sfxData?.sfx ?? []} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
