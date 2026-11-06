import { useState, type ReactNode } from "react";
import type { ProjectDetail, RenderOutput } from "@/api/types";
import { Seg } from "@/editor/controls";
import { Player } from "@/editor/Player";
import { useEditor } from "@/editor/store";
import { fmtDuration } from "@/lib/format";
import { useAgentChat } from "./store";
import { clipScore } from "./Turn";

const BUSY = ["queued", "analyzing"];

/** Beside the chat: the video from the moment it's dropped in, then its clips as they'll look with
 * every edit so far, then the finished files. Worked out from the project's real state. */
export function PreviewPanel({ projectId, project, storeReady, uploading, justUploaded, files }: {
  projectId: string | null;
  project: ProjectDetail | undefined;
  /** the editor's store holds this project's clips */
  storeReady: boolean;
  uploading: { url: string | null } | null;
  /** this tab just finished sending it, and the server hasn't said so yet: up to one poll's wait */
  justUploaded: boolean;
  files: RenderOutput[];
}) {
  const view = useAgentChat((s) => s.previewView);
  const hidden = useAgentChat((s) => s.previewHidden);
  const setView = useAgentChat((s) => s.setPreviewView);
  const setHidden = useAgentChat((s) => s.setPreviewHidden);

  if (!uploading && !projectId) return null;

  const current = project && project.id === projectId ? project : undefined;
  const ready = !uploading && current?.status === "ready" && !!current.analysis?.clips.length && storeReady;
  const canSwitch = ready && files.length > 0;

  function body() {
    if (uploading) {
      return <Watching key="local" src={uploading.url} note="Uploading. You can watch it while you wait, and your clips will show here once it's analyzed." />;
    }
    if (!current) return <Loading />;
    if (current.status === "uploading") {
      return <Watching key="waiting" src={`/media/${current.id}/source`} note={justUploaded ? "Uploaded. Getting it ready to analyze." : "This video hasn't finished uploading yet."} />;
    }
    if (BUSY.includes(current.status)) {
      return <Watching key="analyzing" src={`/media/${current.id}/source`} note="Finding the best moments. You can watch it while you wait, and your clips will show here when it's done." />;
    }
    if (current.status === "failed") return <Empty>There's nothing to preview, because the analysis stopped. The chat says why.</Empty>;
    if (!current.analysis?.clips.length) return <Empty>This video has no clips to preview.</Empty>;
    if (!storeReady) return <Loading />;
    // the newest file comes first, so a new render starts again from the top
    return canSwitch && view === "files" ? <Files key={files[0].file} files={files} /> : <ClipPreview projectId={current.id} />;
  }

  return (
    <section className="glass agent-card agent-preview" aria-label="Preview">
      <div className="agent-preview-head">
        <h2>Preview</h2>
        <button className="link-btn" aria-expanded={!hidden} onClick={() => setHidden(!hidden)}>{hidden ? "Show" : "Hide"}</button>
      </div>
      {!hidden && canSwitch && (
        <Seg
          options={[{ id: "clip", label: "Edits" }, { id: "files", label: `Rendered ${files.length}` }]}
          value={view}
          onChange={setView}
        />
      )}
      {!hidden && body()}
    </section>
  );
}

function Loading() {
  return (
    <div className="agent-preview-frame" role="status" aria-label="Loading the preview">
      <span className="spin" aria-hidden />
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="agent-preview-frame">
      <p className="muted" role="status">{children}</p>
    </div>
  );
}

/** The whole video, before there are clips. Not every format plays in a browser, and that's fine here. */
function Watching({ src, note }: { src: string | null; note: string }) {
  const [broken, setBroken] = useState(false);
  return (
    <>
      {src && !broken ? (
        <video className="agent-preview-video" src={src} controls playsInline preload="metadata" aria-label="Your video" onError={() => setBroken(true)} />
      ) : (
        <div className="agent-preview-frame" aria-hidden />
      )}
      <p className="muted agent-preview-note" role="status">{note}</p>
    </>
  );
}

function Stepper({ label, at, total, onMove, what }: { label: string; at: number; total: number; onMove: (i: number) => void; what: string }) {
  return (
    <div className="agent-preview-nav">
      <button className="btn ghost icon" aria-label={`Previous ${what}`} disabled={at <= 0} onClick={() => onMove(at - 1)}>
        <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 6-6 6 6 6" /></svg>
      </button>
      <span role="status">{label}</span>
      <button className="btn ghost icon" aria-label={`Next ${what}`} disabled={at >= total - 1} onClick={() => onMove(at + 1)}>
        <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6" /></svg>
      </button>
    </div>
  );
}

/** The editor's own player, on the same store, so a change from the chat shows here straight away. */
function ClipPreview({ projectId }: { projectId: string }) {
  const clips = useEditor((s) => s.clips);
  const active = useEditor((s) => s.activeIdx);
  const edit = useEditor((s) => s.edits[s.activeIdx]);
  const setActive = useEditor((s) => s.setActive);
  const [broken, setBroken] = useState(false);
  const clip = clips[active];
  if (!clip) return null;

  const start = edit?.start ?? clip.start_seconds;
  const end = edit?.end ?? clip.end_seconds;
  const score = clipScore(clip);
  return (
    <>
      <div className="agent-preview-stage">
        {broken ? (
          <div className="agent-preview-frame">
            <img src={`/media/${projectId}/frame.jpg?t=${Math.floor(start)}`} alt="" onError={(e) => { e.currentTarget.style.display = "none"; }} />
          </div>
        ) : (
          <Player compact onVideoError={() => setBroken(true)} />
        )}
      </div>
      <Stepper label={`Clip ${active + 1} of ${clips.length}`} at={active} total={clips.length} onMove={setActive} what="clip" />
      <div className="agent-preview-text">
        <p className="decision-title">{clip.title || clip.hook || `Clip ${active + 1}`}</p>
        <p className="muted">
          {fmtDuration(end - start)}
          {score != null && `  ·  Score ${score}`}
          {edit?.keep === false && "  ·  Skipped"}
        </p>
      </div>
      <p className="muted agent-preview-note" role={broken ? "status" : undefined}>
        {broken
          ? "We can't play this video in your browser, so this is a still from the clip. Your rendered clips will play normally."
          : "This is how it'll look with your edits. Sound effects only play in the rendered file."}
      </p>
    </>
  );
}

/** The real files, as they came out of the render. */
function Files({ files }: { files: RenderOutput[] }) {
  const [at, setAt] = useState(0);
  const [broken, setBroken] = useState<string | null>(null);
  const file = files[Math.min(at, files.length - 1)];
  return (
    <>
      <div className="agent-preview-stage">
        {broken === file.file ? (
          <div className="agent-preview-frame">
            <p className="muted" role="alert">We couldn't load this file. Try it again, or download it below.</p>
          </div>
        ) : (
          <video
            key={file.file}
            className="agent-preview-video"
            src={file.url}
            controls
            playsInline
            preload="metadata"
            aria-label={`Rendered clip ${at + 1}`}
            onError={() => setBroken(file.file)}
          />
        )}
      </div>
      <Stepper label={`File ${at + 1} of ${files.length}`} at={at} total={files.length} onMove={setAt} what="file" />
      <div className="agent-preview-text">
        <p className="agent-preview-file">{file.file}</p>
        <p className="muted">{fmtDuration(file.duration)}</p>
      </div>
      <a className="btn ghost small agent-preview-download" href={`${file.url}?download=1`} download>Download</a>
    </>
  );
}
