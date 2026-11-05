import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { jget, jpost } from "@/api/client";
import type { ProjectDetail, SfxItem } from "@/api/types";
import { useToast } from "@/components/Toast";
import { useSSE } from "@/lib/sse";
import { fmtDuration } from "@/lib/format";
import { flushSave, useEditor } from "@/editor/store";
import { Player } from "@/editor/Player";
import { Timeline } from "@/editor/Timeline";
import { ClipRail, StageHeader } from "@/editor/ClipList";
import { ClipCard } from "@/editor/ClipCard";
import { Inspector } from "@/editor/Inspector";
import { RenderButton, RenderOutputs, RenderSync, SaveStatus, useKeptRanges } from "@/editor/RenderPanel";

type ViewMode = "review" | "edit";
type Filter = "all" | "kept" | "skipped";

export function Editor() {
  const { id = "" } = useParams();
  // land on the finished-clips overview first; per-clip fine-tuning is an opt-in "Edit"
  const [view, setView] = useState<ViewMode>("review");
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => {
    setView("review");
    setFilter("all");
  }, [id]);
  const setActive = useEditor((s) => s.setActive);
  function editClip(idx: number) {
    setActive(idx);
    setView("edit");
    window.scrollTo({ top: 0 });
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
    // never re-open the editor from a cached copy: it predates whatever was saved since,
    // and the next autosave would write that older state back over the newer one
    gcTime: 0,
  });
  // a save still waiting on its debounce goes out before the editor leaves this project
  useEffect(() => () => flushSave(), [id]);
  const { data: sfxData } = useQuery({
    queryKey: ["sfx"],
    queryFn: () => jget<{ sfx: SfxItem[] }>("/api/sfx"),
    staleTime: 300_000,
  });

  const qc = useQueryClient();
  const toast = useToast();
  const retry = useMutation({
    // { any_language: true } is "Analyze anyway": it skips the English check
    mutationFn: (body: { any_language: true } | void) => jpost(`/api/projects/${id}/analyze`, body ?? undefined),
    onSuccess: () => {
      // a successful re-run clears the saved edits server-side; never re-apply the cached
      // ones to the new clips (they're keyed by clip number)
      qc.removeQueries({ queryKey: ["edit", id] });
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => toast((e as Error).message || "Couldn't restart the analysis.", "error"),
  });
  function reanalyze() {
    if (
      confirm(
        "Re-analyze this video with the current clip finder?\n\n" +
          "You'll get a new set of clips, and your trims, framing and caption settings for the " +
          "current clips are cleared. Rendered files stay. If the re-analysis fails, nothing changes.",
      )
    ) {
      retry.mutate();
    }
  }

  const analyzing = project ? ["queued", "analyzing", "uploading"].includes(project.status) : false;
  const analyzeSSE = useSSE<{ pct: number; msg: string }>(`/api/projects/${id}/analyze/events`, analyzing);
  // the project query polls every 2.5s while analyzing; use its job as a floor so the bar
  // still moves if the event stream is down
  const liveJob = project?.job?.status === "queued" || project?.job?.status === "running" ? project.job : null;
  const analyzePct = Math.max(analyzeSSE?.pct ?? 0, liveJob?.pct ?? 0);
  const analyzeMsg = (analyzeSSE?.pct ?? 0) >= (liveJob?.pct ?? 0) ? analyzeSSE?.msg : liveJob?.msg;

  const initEditor = useEditor((s) => s.init);
  const ready = project?.status === "ready" && project.analysis && edit !== undefined;
  const storeReady = useEditor((s) => s.projectId === id && s.clips.length > 0);

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
  const markIn = useEditor((s) => s.markIn);
  const markOut = useEditor((s) => s.markOut);
  useEffect(() => {
    if (!ready || view !== "edit") return;
    const onKey = (ev: KeyboardEvent) => {
      const t = ev.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA") return;
      const v = document.querySelector<HTMLVideoElement>(".stage video[data-main]");
      if (ev.key === " ") {
        ev.preventDefault();
        if (v) v.paused ? void v.play() : v.pause();
      } else if (ev.key === "ArrowRight") {
        setPlayhead(playhead + (ev.shiftKey ? 5 : 1 / 30));
      } else if (ev.key === "ArrowLeft") {
        setPlayhead(Math.max(0, playhead - (ev.shiftKey ? 5 : 1 / 30)));
      } else if (ev.key === "i") {
        markIn(playhead);
      } else if (ev.key === "o") {
        markOut(playhead);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ready, view, playhead, setPlayhead, markIn, markOut]);

  if (!project) {
    return <div style={{ display: "grid", placeItems: "center", minHeight: "40vh" }}><div className="spin" /></div>;
  }

  const hasClips = !!(ready && storeReady && project.analysis && project.analysis.clips.length > 0);

  return (
    <div>
      <RenderSync />
      <div className="editor-head">
        <Link to="/app" className="muted" style={{ fontSize: "var(--fs-sm)", whiteSpace: "nowrap" }}>← Projects</Link>
        <h1 style={{ fontSize: "var(--fs-xl)" }}>{project.name}</h1>
        {project.duration > 0 && <span className="muted" style={{ fontSize: "var(--fs-sm)" }}>{fmtDuration(project.duration)}</span>}
        {hasClips && view === "edit" && (
          <span className="editor-head-actions">
            <SaveStatus />
            <button className="btn ghost" onClick={() => setView("review")}>All clips</button>
            <RenderButton />
          </span>
        )}
      </div>

      {analyzing && (
        <div className="glass" style={{ padding: "var(--sp-5)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-sm)", marginBottom: 10 }}>
            <span>{analyzeMsg || "Getting started…"}</span>
            <span className="muted">{analyzePct}%</span>
          </div>
          <div style={{ height: 5, background: "var(--sub-bg)", borderRadius: 99, overflow: "hidden" }}>
            <div style={{ width: `${analyzePct}%`, height: "100%", background: "var(--accent)", transition: "width .3s" }} />
          </div>
        </div>
      )}

      {project.status === "failed" && project.job?.language && (
        <div className="glass" style={{ padding: "var(--sp-5)", borderColor: "color-mix(in srgb, var(--warn) 40%, transparent)" }}>
          <b>We stopped before analyzing this video.</b>
          <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>{project.error}</p>
          <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>If it is in English, or you'd like to try anyway, you can still analyze it.</p>
          <button className="btn ghost" disabled={retry.isPending} onClick={() => retry.mutate({ any_language: true })}>
            {retry.isPending ? "Starting…" : "Analyze anyway"}
          </button>
        </div>
      )}

      {project.status === "failed" && !project.job?.language && (
        <div className="glass" style={{ padding: "var(--sp-5)", borderColor: "color-mix(in srgb, var(--danger) 40%, transparent)" }}>
          <b style={{ color: "var(--danger)" }}>Analysis failed.</b>
          <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>{project.error || "Something went wrong while analyzing."}</p>
          <button className="btn" disabled={retry.isPending} onClick={() => retry.mutate()}>
            {retry.isPending ? "Restarting…" : "Retry analysis"}
          </button>
        </div>
      )}

      {project.status === "ready" && project.job?.status === "error" && (
        <div className="glass" role="status" style={{ padding: "var(--sp-3) var(--sp-4)", marginBottom: "var(--sp-4)", display: "flex", gap: "var(--sp-3)", alignItems: "center", flexWrap: "wrap", borderColor: "color-mix(in srgb, var(--danger) 40%, transparent)" }}>
          <span style={{ flex: "1 1 280px", fontSize: "var(--fs-sm)" }}>
            <b style={{ color: "var(--danger)" }}>{project.job.language ? "Re-analysis stopped." : "Re-analysis failed."}</b>{" "}
            <span className="muted">{project.job.error || "Something went wrong."} Your previous clips are still here.</span>
          </span>
          {project.job.language ? (
            <button className="btn ghost" disabled={retry.isPending} onClick={() => retry.mutate({ any_language: true })}>Analyze anyway</button>
          ) : (
            <button className="btn ghost" disabled={retry.isPending} onClick={() => retry.mutate()}>Try again</button>
          )}
        </div>
      )}

      {project.status === "ready" && project.analysis && project.analysis.clips.length === 0 && (
        <div className="glass" style={{ padding: "var(--sp-5)" }}>
          <p className="muted" style={{ margin: 0 }}>No standalone clips found in this one. Try a longer or livelier source.</p>
          <button className="btn ghost" style={{ marginTop: "var(--sp-3)" }} disabled={retry.isPending} onClick={reanalyze}>
            Re-analyze
          </button>
        </div>
      )}

      {hasClips && view === "review" && (
        <ReviewView
          filter={filter}
          setFilter={setFilter}
          onEdit={editClip}
          onFineTune={() => editClip(useEditor.getState().activeIdx)}
          onReanalyze={reanalyze}
          reanalyzing={retry.isPending}
          requested={project.job?.requested ?? null}
        />
      )}

      {hasClips && view === "edit" && (
        <div className="ed-layout">
          <ClipRail />
          <div className="stage">
            <section className="glass stage-card" aria-label="Preview">
              <StageHeader />
              <Player />
            </section>
            <Timeline />
          </div>
          <div className="ed-side">
            <Inspector sfx={sfxData?.sfx ?? []} />
            <RenderOutputs />
          </div>
        </div>
      )}
    </div>
  );
}

function ReviewView({
  filter,
  setFilter,
  onEdit,
  onFineTune,
  onReanalyze,
  reanalyzing,
  requested,
}: {
  filter: Filter;
  setFilter: (f: Filter) => void;
  onEdit: (i: number) => void;
  onFineTune: () => void;
  onReanalyze: () => void;
  reanalyzing: boolean;
  requested: number | null;
}) {
  const clips = useEditor((s) => s.clips);
  const edits = useEditor((s) => s.edits);
  const setAllKeep = useEditor((s) => s.setAllKeep);
  const ranges = useKeptRanges();

  const keptIdx = useMemo(() => clips.map((_, i) => i).filter((i) => edits[i]?.keep !== false), [clips, edits]);
  const shown = clips
    .map((_, i) => i)
    .filter((i) => (filter === "all" ? true : filter === "kept" ? keptIdx.includes(i) : !keptIdx.includes(i)));
  const total = ranges.reduce((s, r) => s + (Number(r.end) - Number(r.start)), 0);
  const allKept = keptIdx.length === clips.length;
  const vertical = ranges.filter((r) => r.vertical).length;
  const captioned = ranges.filter((r) => r.captions).length;

  const counts: Record<Filter, number> = { all: clips.length, kept: keptIdx.length, skipped: clips.length - keptIdx.length };

  return (
    <>
      <div className="review-toolbar">
        <h2 style={{ fontSize: "var(--fs-lg)" }}>
          {requested && clips.length < requested
            ? `${clips.length} strong moment${clips.length === 1 ? "" : "s"}`
            : `${clips.length} moment${clips.length === 1 ? "" : "s"} found`}
          {requested && clips.length < requested && (
            <span className="muted" style={{ fontSize: "var(--fs-sm)", fontWeight: 400, marginLeft: 8 }}>
              you asked for {requested} — the rest didn't clear the quality bar
            </span>
          )}
        </h2>
        <div role="group" aria-label="Filter clips" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {(["all", "kept", "skipped"] as Filter[]).map((f) => (
            <button key={f} className={`chip${filter === f ? " on" : ""}`} aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f[0].toUpperCase() + f.slice(1)} {counts[f]}
            </button>
          ))}
        </div>
        <span style={{ marginLeft: "auto", display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
          <button
            className="btn ghost"
            onClick={onReanalyze}
            disabled={reanalyzing}
            title="Run the latest clip finder on this video again"
          >
            {reanalyzing ? "Starting…" : "Re-analyze"}
          </button>
          <button className="btn ghost" onClick={() => setAllKeep(!allKept)}>
            {allKept ? "Skip all" : "Keep all"}
          </button>
        </span>
      </div>

      {shown.length === 0 ? (
        <div className="glass" style={{ padding: "var(--sp-5)" }}>
          <p className="muted" style={{ margin: 0 }}>No {filter} clips.</p>
        </div>
      ) : (
        <div className="review-grid">
          {shown.map((i) => (
            <ClipCard key={i} index={i} onEdit={onEdit} />
          ))}
        </div>
      )}

      <div style={{ marginTop: "var(--sp-4)" }}>
        <RenderOutputs />
      </div>

      <div className="render-bar">
        <span style={{ fontFamily: "'Space Grotesk'", fontWeight: 600 }}>
          {ranges.length} clip{ranges.length === 1 ? "" : "s"} · {fmtDuration(total)} total
        </span>
        {ranges.length > 0 && (
          <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
            {vertical === ranges.length ? "9:16 vertical" : vertical === 0 ? "original framing" : `${vertical} vertical`}
            {" · "}
            {captioned === ranges.length ? "captions on" : captioned === 0 ? "no captions" : `${captioned} captioned`}
          </span>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: "var(--sp-2)" }}>
          <SaveStatus />
          <button className="btn ghost" onClick={onFineTune}>Fine-tune</button>
          <RenderButton />
        </span>
      </div>
    </>
  );
}
