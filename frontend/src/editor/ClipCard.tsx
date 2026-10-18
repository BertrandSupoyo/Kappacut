import { useId, useRef, useState, type SyntheticEvent } from "react";
import { useEditor } from "./store";
import { clipVisualStyle, clipLoopHandlers } from "./clipVisual";
import { ColorGrade, gradeId } from "./colorGrade";
import { fmtDuration } from "@/lib/format";

export function ClipCard({ index, onEdit }: { index: number; onEdit: (i: number) => void }) {
  const projectId = useEditor((s) => s.projectId);
  const clip = useEditor((s) => s.clips[index]);
  const edit = useEditor((s) => s.edits[index]);
  const toggleKeep = useEditor((s) => s.toggleKeep);
  const gid = gradeId(useId());
  const ref = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [progress, setProgress] = useState(0);

  if (!clip || !edit) return null;

  const start = edit.start;
  const end = edit.end;
  const { vertical, mode, blurFill, cropStyle, bgStyle, fgWrapStyle, innerStyle } = clipVisualStyle(edit.look, edit.color, gid);
  const loop = clipLoopHandlers(start, end);
  // a still from the clip itself, so the card reads before any video has loaded
  const poster = `/media/${projectId}/frame.jpg?t=${Math.floor(start)}`;

  function toggle() {
    const v = ref.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => setPlaying(false));
    else v.pause();
  }

  const video = (
    <video
      ref={ref}
      src={`/media/${projectId}/source`}
      poster={poster}
      style={innerStyle}
      preload="metadata"
      muted={muted}
      playsInline
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
      onLoadedMetadata={loop.onLoadedMetadata}
      onTimeUpdate={(e: SyntheticEvent<HTMLVideoElement>) => {
        loop.onTimeUpdate(e);
        setProgress(Math.min(1, Math.max(0, (e.currentTarget.currentTime - start) / Math.max(0.1, end - start))));
      }}
    />
  );

  return (
    <article className="glass clip-card" style={{ opacity: edit.keep ? 1 : 0.5 }}>
      <ColorGrade id={gid} color={edit.color} />
      {/* same max width as the 9:16 frame, so the overlays sit on its corners */}
      <div className="clip-frame" style={{ maxWidth: vertical ? "min(320px, 46vh)" : undefined }}>
        <span className="badge" style={{ position: "absolute", top: 8, left: 8, zIndex: 2 }}>{fmtDuration(end - start)}</span>
        {clip.beat_type && <span className="badge" style={{ position: "absolute", top: 8, right: 8, zIndex: 2 }}>{clip.beat_type}</span>}
        <div style={cropStyle}>
          {blurFill && (
            <video
              src={`/media/${projectId}/source`}
              poster={poster}
              style={bgStyle}
              preload="metadata"
              autoPlay
              muted
              loop
              playsInline
              {...loop}
            />
          )}
          {mode === "cropfill" && vertical ? <div style={fgWrapStyle}>{video}</div> : video}
        </div>
        <button
          className={`clip-play${playing ? " is-playing" : ""}`}
          onClick={toggle}
          aria-label={playing ? `Pause clip ${index + 1}` : `Play clip ${index + 1}`}
        >
          <span className="clip-play-dot" aria-hidden>
            {playing ? (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" /></svg>
            ) : (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13l11-6.5z" /></svg>
            )}
          </span>
        </button>
        <button
          className="clip-mute"
          onClick={() => setMuted((m) => !m)}
          aria-label={muted ? "Turn sound on" : "Mute"}
          aria-pressed={!muted}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M11 5 6 9H3v6h3l5 4z" />
            {muted ? <path d="m16 9 5 6M21 9l-5 6" /> : <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />}
          </svg>
        </button>
        <span className="clip-progress" aria-hidden><i style={{ width: `${progress * 100}%` }} /></span>
      </div>

      <div style={{ marginTop: "var(--sp-3)", minHeight: 44, flex: 1 }}>
        <div className="clamp-2" style={{ fontFamily: "'Space Grotesk'", fontWeight: 600, fontSize: "var(--fs-sm)" }}>
          {index + 1}. {clip.title || clip.hook || "Moment"}
        </div>
        <div className="muted" style={{ fontSize: "var(--fs-2xs)", marginTop: 2, display: "flex", alignItems: "center", gap: 6 }}>
          <span>{fmtDuration(start)} – {fmtDuration(end)}</span>
          {clip.virality !== undefined ? (
            <span className="score-chip">{clip.virality}<small>/100</small></span>
          ) : (
            <span>· score {clip.score.toFixed(1)}</span>
          )}
        </div>
      </div>

      <div style={{ display: "flex", gap: "var(--sp-2)", marginTop: "var(--sp-3)" }}>
        <button
          className="btn ghost"
          style={{
            flex: 1,
            padding: "6px 0",
            fontSize: "var(--fs-xs)",
            borderColor: edit.keep ? "var(--accent)" : "var(--border)",
            color: edit.keep ? "var(--accent)" : "var(--text-mut)",
          }}
          onClick={() => toggleKeep(index)}
          aria-pressed={edit.keep}
          title={edit.keep ? "In the render — click to skip" : "Left out of the render — click to keep"}
        >
          {edit.keep ? "✓ Kept" : "Skipped"}
        </button>
        <button className="btn" style={{ flex: 1, padding: "6px 0", fontSize: "var(--fs-xs)" }} onClick={() => onEdit(index)}>
          Edit
        </button>
      </div>
    </article>
  );
}
